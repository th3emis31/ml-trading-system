from __future__ import annotations

import base64
import json
import os
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


class ExternalVoiceProviders:
    """Feature-flagged bridge for external STT/TTS providers."""

    def __init__(self, timeout_seconds: int = 20):
        self.timeout_seconds = int(max(5, timeout_seconds))

    @staticmethod
    def _env(name: str, default: str | None = None) -> str | None:
        value = os.getenv(name)
        if value is None:
            return default
        clean = str(value).strip()
        return clean if clean else default

    def _post_json(self, url: str, headers: dict[str, str], payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urlrequest.Request(url=url, data=data, headers=headers, method="POST")
        try:
            with urlrequest.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urlerror.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"provider_http_error:{exc.code}:{detail[:300]}")
        except urlerror.URLError as exc:
            raise RuntimeError(f"provider_network_error:{exc.reason}")
        except ValueError as exc:
            raise RuntimeError(f"provider_response_parse_error:{exc}")

    def _post_binary(self, url: str, headers: dict[str, str], body: bytes) -> bytes:
        req = urlrequest.Request(url=url, data=body, headers=headers, method="POST")
        try:
            with urlrequest.urlopen(req, timeout=self.timeout_seconds) as resp:
                return resp.read()
        except urlerror.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"provider_http_error:{exc.code}:{detail[:300]}")
        except urlerror.URLError as exc:
            raise RuntimeError(f"provider_network_error:{exc.reason}")

    def provider_status(self) -> dict:
        tts_provider = str(self._env("VOICE_TTS_PROVIDER", "browser") or "browser").lower()
        stt_provider = str(self._env("VOICE_STT_PROVIDER", "browser") or "browser").lower()
        return {
            "tts_provider": tts_provider,
            "stt_provider": stt_provider,
            "elevenlabs_configured": bool(self._env("ELEVENLABS_API_KEY")),
            "azure_tts_configured": bool(self._env("AZURE_SPEECH_KEY") and self._env("AZURE_SPEECH_REGION")),
            "deepgram_configured": bool(self._env("DEEPGRAM_API_KEY")),
        }

    def synthesize(self, text: str, provider: str | None = None) -> dict:
        clean_text = str(text or "").strip()
        if not clean_text:
            raise ValueError("text is required")

        selected = str(provider or self._env("VOICE_TTS_PROVIDER", "browser") or "browser").lower()
        if selected in {"browser", "none", "disabled"}:
            return {"provider": "browser_speech_synthesis", "text": clean_text, "mime_type": None, "audio_base64": None}
        if selected == "elevenlabs":
            return self._synthesize_elevenlabs(clean_text)
        if selected == "azure":
            return self._synthesize_azure(clean_text)
        raise ValueError(f"unsupported_tts_provider:{selected}")

    def _synthesize_elevenlabs(self, text: str) -> dict:
        api_key = self._env("ELEVENLABS_API_KEY")
        voice_id = self._env("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
        model_id = self._env("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
        if not api_key:
            raise RuntimeError("elevenlabs_not_configured")

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        payload = {
            "text": text,
            "model_id": model_id,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
            "xi-api-key": api_key,
        }
        req = urlrequest.Request(url=url, data=data, headers=headers, method="POST")
        try:
            with urlrequest.urlopen(req, timeout=self.timeout_seconds) as resp:
                audio = resp.read()
        except urlerror.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"provider_http_error:{exc.code}:{detail[:300]}")
        except urlerror.URLError as exc:
            raise RuntimeError(f"provider_network_error:{exc.reason}")
        return {
            "provider": "elevenlabs",
            "mime_type": "audio/mpeg",
            "audio_base64": base64.b64encode(audio).decode("utf-8"),
            "text": text,
        }

    def _synthesize_azure(self, text: str) -> dict:
        key = self._env("AZURE_SPEECH_KEY")
        region = self._env("AZURE_SPEECH_REGION")
        voice = self._env("AZURE_TTS_VOICE", "en-US-JennyNeural")
        if not key or not region:
            raise RuntimeError("azure_tts_not_configured")
        url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
        ssml = (
            "<speak version='1.0' xml:lang='en-US'>"
            f"<voice name='{voice}'>{text}</voice>"
            "</speak>"
        ).encode("utf-8")
        headers = {
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3",
            "User-Agent": "jarvis-voice",
        }
        audio = self._post_binary(url, headers, ssml)
        return {
            "provider": "azure",
            "mime_type": "audio/mpeg",
            "audio_base64": base64.b64encode(audio).decode("utf-8"),
            "text": text,
        }

    def transcribe(self, audio_base64: str, mime_type: str = "audio/webm", provider: str | None = None) -> dict:
        raw = str(audio_base64 or "").strip()
        if not raw:
            raise ValueError("audio_base64 is required")
        selected = str(provider or self._env("VOICE_STT_PROVIDER", "browser") or "browser").lower()
        if selected in {"browser", "none", "disabled"}:
            raise ValueError("browser_stt_selected_use_client_transcript")
        if selected != "deepgram":
            raise ValueError(f"unsupported_stt_provider:{selected}")
        api_key = self._env("DEEPGRAM_API_KEY")
        if not api_key:
            raise RuntimeError("deepgram_not_configured")

        try:
            audio_bytes = base64.b64decode(raw)
        except ValueError:
            raise ValueError("invalid_audio_base64")

        params = urlparse.urlencode({"model": "nova-2", "smart_format": "true", "punctuate": "true"})
        url = f"https://api.deepgram.com/v1/listen?{params}"
        headers = {
            "Authorization": f"Token {api_key}",
            "Content-Type": str(mime_type or "audio/webm"),
        }
        req = urlrequest.Request(url=url, data=audio_bytes, headers=headers, method="POST")
        try:
            with urlrequest.urlopen(req, timeout=self.timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urlerror.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"provider_http_error:{exc.code}:{detail[:300]}")
        except urlerror.URLError as exc:
            raise RuntimeError(f"provider_network_error:{exc.reason}")
        except ValueError as exc:
            raise RuntimeError(f"provider_response_parse_error:{exc}")

        channels = (((payload.get("results") or {}).get("channels")) or [])
        alternatives = (((channels[0] if channels else {}).get("alternatives")) or [])
        best = alternatives[0] if alternatives else {}
        transcript = str(best.get("transcript") or "").strip()
        confidence = best.get("confidence")
        return {
            "provider": "deepgram",
            "transcript": transcript,
            "confidence": float(confidence) if confidence is not None else None,
            "raw": payload,
        }
