@echo off
REM Start Claude Code in THIS project, whatever directory you are in when you run it.
REM
REM Why this file exists: Claude started outside the project root loads no CLAUDE.md,
REM no skills and no hooks, and relative paths then resolve into your home folder.
REM That is not a hypothetical — it created a stray data\ directory that the app wrote
REM to while the real project file sat untouched. Double-click this, or run it from
REM anywhere, and the working directory is always correct.
cd /d "%~dp0"
echo Starting Claude in %CD%
claude %*
