// vscode-nova: VS Code extension for the NOVA programming language.
//
// What this extension does on activation:
//   1. Reads `nova.python.path` (default `python3`) + module configs.
//   2. If `nova.lsp.enabled` is true, spawns
//      `python -m nova_lsp [args...]` over stdio and attaches a
//      LanguageClient.
//   3. Registers a DebugAdapterDescriptorFactory for the `nova`
//      debugger type that spawns `python -m nova_dap [args...]` on
//      DAP session start.
//
// What it deliberately does NOT do:
//   - Auto-install the Python servers. The user must run
//     `pip install -e tools/nova-lsp tools/nova-dap` inside a venv
//     and point `nova.python.path` at that venv's interpreter.
//   - Ship the tree-sitter WASM parser. v0.1 uses the TextMate
//     grammar in `syntaxes/nova.tmLanguage.json` for syntax
//     highlighting. Tree-sitter WASM bundling is deferred (see
//     README.md "Roadmap").
//
// All identifiers, comments, and error messages here intentionally
// avoid mentioning any specific upstream model / tool branding.

import * as cp from 'child_process';
import * as path from 'path';
import * as vscode from 'vscode';
import {
    LanguageClient,
    LanguageClientOptions,
    ServerOptions,
    State,
    StreamInfo,
    TransportKind,
} from 'vscode-languageclient/node';

const NOVA_LANG_ID = 'nova';
const NOVA_DEBUG_TYPE = 'nova';
const OUTPUT_CHANNEL_NAME = 'NOVA Language Server';

let client: LanguageClient | undefined;
let outputChannel: vscode.OutputChannel | undefined;

interface NovaConfig {
    pythonPath: string;
    lspEnabled: boolean;
    lspModule: string;
    lspArgs: string[];
    dapModule: string;
    dapArgs: string[];
    traceServer: 'off' | 'messages' | 'verbose';
}

/**
 * Read user / workspace config once. Falls back to safe defaults.
 */
function readConfig(): NovaConfig {
    const cfg = vscode.workspace.getConfiguration('nova');
    const traceRaw = cfg.get<string>('trace.server', 'off');
    const trace: 'off' | 'messages' | 'verbose' =
        traceRaw === 'messages' || traceRaw === 'verbose' ? traceRaw : 'off';
    return {
        pythonPath: cfg.get<string>('python.path', 'python3'),
        lspEnabled: cfg.get<boolean>('lsp.enabled', true),
        lspModule: cfg.get<string>('lsp.module', 'nova_lsp'),
        lspArgs: cfg.get<string[]>('lsp.args', []),
        dapModule: cfg.get<string>('dap.module', 'nova_dap'),
        dapArgs: cfg.get<string[]>('dap.args', []),
        traceServer: trace,
    };
}

/**
 * Build LanguageClient ServerOptions that spawn the Python LSP via
 * `python -m nova_lsp`. Communication is over stdio.
 */
function buildLspServerOptions(config: NovaConfig): ServerOptions {
    const args = ['-m', config.lspModule, ...config.lspArgs];
    return {
        run: {
            command: config.pythonPath,
            args,
            transport: TransportKind.stdio,
            options: { env: process.env },
        },
        debug: {
            command: config.pythonPath,
            args,
            transport: TransportKind.stdio,
            options: { env: process.env },
        },
    };
}

/**
 * Start the LSP client. Returns the client, or undefined if disabled.
 */
async function startLanguageClient(
    context: vscode.ExtensionContext,
    config: NovaConfig,
): Promise<LanguageClient | undefined> {
    if (!config.lspEnabled) {
        outputChannel?.appendLine(
            '[nova-lsp] disabled via `nova.lsp.enabled`; skipping start.',
        );
        return undefined;
    }

    const serverOptions = buildLspServerOptions(config);
    const clientOptions: LanguageClientOptions = {
        documentSelector: [
            { scheme: 'file', language: NOVA_LANG_ID },
            { scheme: 'untitled', language: NOVA_LANG_ID },
        ],
        synchronize: {
            fileEvents: vscode.workspace.createFileSystemWatcher('**/*.nova'),
            configurationSection: 'nova',
        },
        outputChannel: outputChannel,
        traceOutputChannel: outputChannel,
    };

    const c = new LanguageClient(
        'novaLanguageServer',
        'NOVA Language Server',
        serverOptions,
        clientOptions,
    );

    c.onDidChangeState((evt) => {
        outputChannel?.appendLine(
            `[nova-lsp] state ${State[evt.oldState]} -> ${State[evt.newState]}`,
        );
    });

    try {
        await c.start();
        outputChannel?.appendLine(
            `[nova-lsp] started via ${config.pythonPath} -m ${config.lspModule}`,
        );
        return c;
    } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        outputChannel?.appendLine(`[nova-lsp] start failed: ${msg}`);
        vscode.window.showWarningMessage(
            `NOVA LSP failed to start (${msg}). ` +
                `Check that '${config.pythonPath} -m ${config.lspModule}' ` +
                `runs inside your venv. See the "NOVA Language Server" output ` +
                `channel for details.`,
        );
        return undefined;
    }
}

/**
 * DebugAdapterDescriptorFactory that spawns nova-dap as a child
 * process and connects VS Code's DAP protocol over the child's stdio.
 */
class NovaDebugAdapterDescriptorFactory
    implements vscode.DebugAdapterDescriptorFactory {
    createDebugAdapterDescriptor(
        _session: vscode.DebugSession,
        _executable: vscode.DebugAdapterExecutable | undefined,
    ): vscode.ProviderResult<vscode.DebugAdapterDescriptor> {
        const config = readConfig();
        const args = ['-m', config.dapModule, ...config.dapArgs];
        outputChannel?.appendLine(
            `[nova-dap] spawning ${config.pythonPath} ${args.join(' ')}`,
        );
        return new vscode.DebugAdapterExecutable(config.pythonPath, args, {
            env: process.env as { [key: string]: string },
        });
    }
}

/**
 * Resolves a launch.json `nova` configuration. Fills in sensible
 * defaults if the user didn't provide them.
 */
class NovaDebugConfigurationProvider
    implements vscode.DebugConfigurationProvider {
    resolveDebugConfiguration(
        folder: vscode.WorkspaceFolder | undefined,
        config: vscode.DebugConfiguration,
        _token?: vscode.CancellationToken,
    ): vscode.ProviderResult<vscode.DebugConfiguration> {
        if (!config.type && !config.request && !config.name) {
            // empty launch.json: synthesise a default
            const editor = vscode.window.activeTextEditor;
            if (editor && editor.document.languageId === NOVA_LANG_ID) {
                config.type = NOVA_DEBUG_TYPE;
                config.name = 'Launch NOVA program';
                config.request = 'launch';
                const folderPath = folder?.uri.fsPath ?? '';
                const basename = folder
                    ? path.basename(folder.uri.fsPath)
                    : 'program';
                config.program = path.join(folderPath, 'build', basename);
                config.args = [];
                config.cwd = folderPath;
                config.stopOnEntry = false;
                config.console = 'integratedTerminal';
            }
        }
        if (!config.program) {
            vscode.window
                .showErrorMessage(
                    'NOVA launch config needs a "program" field ' +
                        '(path to the compiled NOVA binary).',
                )
                .then(() => undefined);
            return undefined;
        }
        return config;
    }
}

/**
 * Reload LSP on configuration change so the user can edit
 * `nova.python.path` without reopening the workspace.
 */
function watchConfig(context: vscode.ExtensionContext) {
    const disp = vscode.workspace.onDidChangeConfiguration(async (evt) => {
        if (!evt.affectsConfiguration('nova')) {
            return;
        }
        outputChannel?.appendLine(
            '[nova] configuration changed; restarting LSP.',
        );
        await stopClient();
        const fresh = readConfig();
        client = await startLanguageClient(context, fresh);
        if (client) {
            context.subscriptions.push(client);
        }
    });
    context.subscriptions.push(disp);
}

async function stopClient(): Promise<void> {
    if (!client) {
        return;
    }
    try {
        await client.stop();
    } catch (err) {
        outputChannel?.appendLine(
            `[nova-lsp] stop failed: ${err instanceof Error ? err.message : err}`,
        );
    }
    client = undefined;
}

/**
 * Command: nova.restartLanguageServer — restart nova-lsp on demand.
 */
function registerCommands(context: vscode.ExtensionContext) {
    const restartDisp = vscode.commands.registerCommand(
        'nova.restartLanguageServer',
        async () => {
            outputChannel?.appendLine(
                '[nova] restart command invoked by user.',
            );
            await stopClient();
            const config = readConfig();
            client = await startLanguageClient(context, config);
            if (client) {
                context.subscriptions.push(client);
            }
        },
    );
    const showOutputDisp = vscode.commands.registerCommand(
        'nova.showOutput',
        () => {
            outputChannel?.show(true);
        },
    );
    context.subscriptions.push(restartDisp, showOutputDisp);
}

/**
 * Detect whether `python -m nova_lsp` can plausibly run. We don't
 * block activation on this — we just surface a clear hint up-front so
 * users know what to install.
 */
function probePython(config: NovaConfig): void {
    cp.execFile(
        config.pythonPath,
        ['-c', `import ${config.lspModule}`],
        { timeout: 5000 },
        (err) => {
            if (err) {
                outputChannel?.appendLine(
                    `[nova] probe: '${config.pythonPath}' cannot import ` +
                        `${config.lspModule}. Install via ` +
                        `'pip install -e tools/nova-lsp tools/nova-dap' ` +
                        `inside the venv whose interpreter is at ` +
                        `nova.python.path. ` +
                        `Underlying error: ${err.message}`,
                );
            } else {
                outputChannel?.appendLine(
                    `[nova] probe: '${config.pythonPath}' can import ` +
                        `${config.lspModule}.`,
                );
            }
        },
    );
}

export async function activate(
    context: vscode.ExtensionContext,
): Promise<void> {
    outputChannel = vscode.window.createOutputChannel(OUTPUT_CHANNEL_NAME);
    context.subscriptions.push(outputChannel);

    const config = readConfig();
    outputChannel.appendLine(
        `[nova] activate (python.path=${config.pythonPath}, ` +
            `lsp.module=${config.lspModule}, dap.module=${config.dapModule})`,
    );

    probePython(config);

    // 1. Register DAP factory + config provider for the `nova` type
    const dapFactory = new NovaDebugAdapterDescriptorFactory();
    context.subscriptions.push(
        vscode.debug.registerDebugAdapterDescriptorFactory(
            NOVA_DEBUG_TYPE,
            dapFactory,
        ),
    );
    const dapConfigProvider = new NovaDebugConfigurationProvider();
    context.subscriptions.push(
        vscode.debug.registerDebugConfigurationProvider(
            NOVA_DEBUG_TYPE,
            dapConfigProvider,
        ),
    );

    // 2. Commands
    registerCommands(context);

    // 3. Reload on config change
    watchConfig(context);

    // 4. Start LSP last so config-watcher is already wired
    client = await startLanguageClient(context, config);
    if (client) {
        context.subscriptions.push(client);
    }
}

export async function deactivate(): Promise<void> {
    await stopClient();
    if (outputChannel) {
        outputChannel.dispose();
        outputChannel = undefined;
    }
}
