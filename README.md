<p align="center">
  <img src="./assets/Jarvis.png" alt="J.A.R.V.I.S" width="850">
</p>

<div align="center">

### `personal Windows assistant`

local assistant • voice • tools • agents

<br>

<img src="https://img.shields.io/badge/windows-10%20%2F%2011-ff1f2d?style=flat-square&logo=windows&logoColor=white">
<img src="https://img.shields.io/badge/tauri-2-ff1f2d?style=flat-square&logo=tauri&logoColor=white">
<img src="https://img.shields.io/badge/react-ff1f2d?style=flat-square&logo=react&logoColor=white">
<img src="https://img.shields.io/badge/typescript-ff1f2d?style=flat-square&logo=typescript&logoColor=white">
<img src="https://img.shields.io/badge/python-ff1f2d?style=flat-square&logo=python&logoColor=white">
<img src="https://img.shields.io/badge/fastapi-ff1f2d?style=flat-square&logo=fastapi&logoColor=white">
<img src="https://img.shields.io/badge/ollama-ff1f2d?style=flat-square&logo=ollama&logoColor=white">

<br><br>

<img src="https://img.shields.io/badge/status-discontinued-151515?style=for-the-badge&labelColor=ff1f2d">

</div>

---

JARVIS is an experimental personal assistant for Windows, developed with AI. The code was produced with Claude Code and Codex, with human decisions and review.

The interface uses Tauri, React and TypeScript. The backend uses Python and FastAPI with local models through Ollama. Actions go through a tool registry, with confirmation for sensitive operations and result verification.

## Requirements

- Windows 10 or 11 x64 with WebView2.
- Python 3.11 or 3.12.
- Node.js LTS and npm.
- Stable Rust and MSVC build tools.
- Ollama running with at least one installed model.

Local voice, FFmpeg, SearXNG, camera and GPU support are optional.

## Installation

Extract the project and open PowerShell in this folder. If script execution is blocked, allow it only for the current session:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

To install system dependencies as well:

```powershell
.\setup.ps1 -InstallSystemDependencies
```

If they are already installed:

```powershell
.\setup.ps1
```

Check available models and start the application:

```powershell
ollama list
.\start.ps1
```

The API listens on `127.0.0.1:8742` and requires the local session credential. Do not expose this port to the internet.

## Language

In **Settings → Language**, choose **Português (Brasil)** or **English** and save. The preference controls interface text and assistant responses. It is stored in `user.language` as `pt-BR` or `en-US`.

Source documentation, code names, scripts and terminal messages are in English. Portuguese text remains only where it represents the `pt-BR` translation or a Portuguese command/test fixture.

Legacy messages, file names, page content and external diagnostics are not translated automatically.

## Voice

To install local speech recognition and synthesis:

```powershell
.\setup.ps1 -InstallVoice
```

The selected language changes speech recognition. The default installation provides a Portuguese Piper voice. For English speech, configure an English Piper model in `voice.piper_model`; changing the language does not download models automatically.

## Development

To start the web version:

```powershell
.\start.ps1 -Web
```

Or start the backend and frontend in separate terminals:

```powershell
.\.venv\Scripts\python.exe -m backend.main
```

```powershell
cd frontend
npm run dev
```

Automated tests live in `tests/` and `frontend/src/`. Windows command tests use controlled substitutes so they do not open applications, modify personal files or shut down the machine.

## Build

To generate the Windows installer:

```powershell
.\scripts\build-installer.ps1
```

Packages are written to `frontend/src-tauri/target/release/bundle`. A source ZIP does not replace the installer.

## Sharing

Include source code, tests, dependency files, visual assets and `config/settings.example.json`. Do not include:

- `.venv`, `node_modules`, caches or build output.
- `config/settings.json`, `.env` or credentials.
- Databases, conversations, execution logs, captures or personal files.
- Voice models, generated executables or previous archives.

History and memory remain application features. Removing local data from a package does not require removing the code for those features.

## Credits

[fhelipe584](https://github.com/fhelipe584) contributed parts of the backend structure, voice integration and interface.

[OpenJarvis](https://github.com/open-jarvis/OpenJarvis) was used as a reference during development; parts were adapted or replaced for this project.

## Project status

Personal experimental project, marked as discontinued by the author. The code remains available for study and maintenance.
