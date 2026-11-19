const vscode = require('vscode');
const { LanguageClient, TransportKind } = require('vscode-languageclient/node');
const { spawn } = require('child_process');

const PYTHON = 'C:\\Users\\Lq\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe';
const ECL_HOME = 'C:\\Users\\Lq\\Desktop\\myprojects\\NUD重制版';

let client = null;

function activate(context) {
  // ---------- LSP ----------
  const serverOptions = {
    command: PYTHON,
    args: [`${ECL_HOME}\\ecl_lsp.py`, '--stdio'],
    transport: TransportKind.stdio
  };
  const clientOptions = {
    documentSelector: [{ language: 'ecl' }]
  };
  client = new LanguageClient('eclLSP', 'ECL Language Server', serverOptions, clientOptions);
  client.start();
  context.subscriptions.push(client);

  // ---------- 运行当前文件 (F5 / 右键) ----------
  context.subscriptions.push(vscode.commands.registerCommand('ecl.run', () => {
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.languageId !== 'ecl') return;

    // 先保存，保证跑的是最新代码
    editor.document.save().then(() => {
      const file = editor.document.fileName;
      const terminal = vscode.window.createTerminal('ECL');
      terminal.show();
      terminal.sendText(`& "${PYTHON}" "${ECL_HOME}\\ecl.py" "${file}"`);
    });
  }));
}

function deactivate() {
  if (client) return client.stop();
  return undefined;
}

module.exports = { activate, deactivate };
