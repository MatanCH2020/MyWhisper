# PyInstaller runs this on a native Apple Silicon macOS runner.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata
import re

root = Path(SPECPATH).parent
version = re.search(r'__version__ = "([^"]+)"', (root / 'app/version.py').read_text()).group(1)
datas = [(str(root / 'app/assets'), 'assets'), (str(root / 'CHANGELOG.md'), '.')]
for package in ('mlx_whisper', 'wordfreq', 'langcodes', 'tiktoken'):
    datas += collect_data_files(package)
datas += copy_metadata('mlx-whisper')
datas += copy_metadata('mlx')
binaries = collect_dynamic_libs('mlx')
a = Analysis([str(root / 'app/main.py')], pathex=[str(root / 'app')],
    binaries=binaries, datas=datas,
    hiddenimports=['mlx.core', 'mlx_whisper.transcribe', 'mlx_whisper.tokenizer',
                   'ApplicationServices', 'AppKit', 'AVFoundation', 'CoreFoundation', 'Quartz',
                   'scipy.signal', 'onnxruntime', 'tiktoken_ext.openai_public'],
    excludes=['torch', 'mlx_whisper.torch_whisper', 'faster_whisper', 'ctranslate2',
              'chatgpt_auth', 'cloud_http', 'text_editor', 'history_scanner',
              'updater', 'hotkey', 'paste', 'winclipboard', 'delivery', 'fullscreen',
              'PySide6.QtQml', 'PySide6.QtQuick', 'tkinter', 'matplotlib'],
    noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MyWhisper',
          console=False, target_arch='arm64', codesign_identity='-')
collection = COLLECT(exe, a.binaries, a.datas, name='MyWhisper')
app = BUNDLE(collection, name='MyWhisper.app', icon=str(root / 'build/MyWhisper.icns'), bundle_identifier='com.matandigital.mywhisper',
    info_plist={'CFBundleShortVersionString': version.split('b')[0],
                'CFBundleVersion': version.split('b')[0] + '.' + version.split('b')[-1],
                'NSHumanReadableCopyright': 'Matan Digital',
                'LSMinimumSystemVersion': '15.0',
                'NSMicrophoneUsageDescription': 'MyWhisper מקליטה דיבור לתמלול מקומי במחשב שלך.',
                'NSHighResolutionCapable': True})
