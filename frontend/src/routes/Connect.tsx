// Connect route (issue #264): how to point an LLM client at this Blindfold instance --
// the one thing a user must do before the product does anything, previously documented
// only in README.md's Usability section. A primary nav destination (not a Settings tab):
// needed before first use, and returned to later for a second machine or a new tool.
//
// Snippets are rendered, never hardcoded (ADR-0027's scrub-by-construction rule extended
// to this page): host/port come from GET /v1/status's config block (issue #264 adds
// config.host/config.port, mirroring _management_url's own settings.host/settings.port
// source), and the active workspace comes from WorkspaceContext. This page never renders
// a credential, token, store key, or any real value -- only variable names and the
// user's own placeholder text.

import { useEffect, useState } from "react";
import { useWorkspace } from "../components/WorkspaceContext";
import { CopyableSnippet } from "../components/CopyableSnippet";
import { TerminalAlwaysTabs } from "../components/TerminalAlwaysTabs";
import { ExternalLink } from "../components/icons";

const DEFAULT_HOST = "127.0.0.1";
const DEFAULT_PORT = 25463;
const CODEX_ISSUE_URL = "https://github.com/tomorrowflow/blindfold/issues/263";

function ProxyDependencyWarning() {
  return (
    <p className="bf-connect-consequence-banner" role="status" data-testid="connect-consequence-warning">
      Every session that reads this file routes through Blindfold from now on. While
      the proxy is stopped, those sessions fail until you start it again -- from the
      menu bar app's <strong>Start Proxy</strong> row.
    </p>
  );
}

export function Connect() {
  const { activeWorkspace } = useWorkspace();
  const [host, setHost] = useState<string>(DEFAULT_HOST);
  const [port, setPort] = useState<number>(DEFAULT_PORT);

  useEffect(() => {
    let cancelled = false;
    fetch("/v1/status")
      .then((r) => r.json())
      .then((data: { config?: { host?: string; port?: number } }) => {
        if (cancelled) return;
        if (data.config?.host) setHost(data.config.host);
        if (data.config?.port) setPort(data.config.port);
      })
      .catch(() => {
        // Falls back to the compiled-in defaults -- still the real shipped default,
        // never a fabricated value.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const base = `http://${host}:${port}`;
  const workspaceSlug = activeWorkspace?.slug ?? null;
  const showWorkspaceHeader = workspaceSlug !== null && workspaceSlug !== "default";

  const claudeCodeTerminalSnippet = `export ANTHROPIC_BASE_URL=${base}`;
  const claudeCodeCredentialSnippet = `export ANTHROPIC_AUTH_TOKEN=<your-token>`;
  const claudeCodeAlwaysSnippet = JSON.stringify(
    { env: { ANTHROPIC_BASE_URL: base } },
    null,
    2
  );
  const workspaceHeaderSnippet = `export ANTHROPIC_CUSTOM_HEADERS="x-blindfold-workspace: ${workspaceSlug}"`;
  const openAiTerminalSnippet = `export OPENAI_BASE_URL=${base}/v1`;

  return (
    <div className="bf-status-view">
      <h1>Connect</h1>
      <p className="bf-connect-intro" data-testid="connect-intro">
        Point an LLM client at this Blindfold instance. From there it's transparent --
        you keep prompting and reading in real names while Blindfold blindfolds outbound
        traffic and restores the response.
      </p>
      <p className="bf-connect-works-with" data-testid="connect-works-with">
        <strong>Works with:</strong> any tool whose endpoint you can point at a URL --
        CLIs, IDE extensions, scripts. <strong>Doesn't work with:</strong> apps whose
        endpoint can't be redirected (claude.ai web, ChatGPT desktop/mobile) -- those are
        out of scope by design.
      </p>

      <section className="bf-connect-card bf-card" data-testid="connect-card-claude-code">
        <h2 className="bf-card-title">Claude Code</h2>
        <p className="bf-card-subtitle">
          Two variables: a base URL, and (optionally) a credential.
        </p>
        <TerminalAlwaysTabs
          testId="connect-claude-code"
          terminal={
            <>
              <CopyableSnippet code={claudeCodeTerminalSnippet} label="ANTHROPIC_BASE_URL export" />
              <p>
                If you're logged in with a claude.ai subscription, that's all you need --
                requests route through Blindfold and your subscription's usage limits and
                billing still apply.
              </p>
              <p>
                If you'd rather use a gateway credential instead of your subscription, also
                set one of <code>ANTHROPIC_AUTH_TOKEN</code> (bearer token) or{" "}
                <code>ANTHROPIC_API_KEY</code> (sent as <code>x-api-key</code>). Setting a
                credential variable replaces the subscription for that session, billed per
                token to whoever owns the credential:
              </p>
              <CopyableSnippet code={claudeCodeCredentialSnippet} label="ANTHROPIC_AUTH_TOKEN export" />
              {showWorkspaceHeader && (
                <>
                  <p>
                    Targets the <strong>{activeWorkspace?.name}</strong> workspace instead of
                    the default one:
                  </p>
                  <CopyableSnippet code={workspaceHeaderSnippet} label="ANTHROPIC_CUSTOM_HEADERS export" />
                </>
              )}
            </>
          }
          always={
            <>
              <CopyableSnippet code={claudeCodeAlwaysSnippet} label="settings.json env block" />
              <p>
                Recommended: <code>~/.claude/settings.json</code> (applies to every project
                on this machine). Also valid: <code>.claude/settings.json</code> (project,
                committed -- never put a credential here) or{" "}
                <code>.claude/settings.local.json</code> (project, gitignored).
              </p>
              <ProxyDependencyWarning />
            </>
          }
        />
        <div className="bf-connect-good-to-know" data-testid="connect-claude-code-notes">
          <h3>What still bypasses Blindfold</h3>
          <p>
            Two pieces of Claude Code's own traffic call <code>api.anthropic.com</code>{" "}
            directly and ignore <code>ANTHROPIC_BASE_URL</code>, so they never reach
            Blindfold: the fast-mode availability check, and the WebFetch tool's domain-safety
            check.
          </p>
          <h3>Good to know</h3>
          <ul>
            <li>
              Blindfold doesn't implement <code>POST /v1/messages/count_tokens</code>. Claude
              Code counts context usage through inference requests instead -- still fully
              blindfolded, just an extra inference request per count.
            </li>
            <li>
              Claude Code sends a harmless <code>HEAD /api/hello</code> connection probe at
              startup; nothing to do about it. Leave{" "}
              <code>CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY</code> unset -- Blindfold
              doesn't serve <code>GET /v1/models</code>.
            </li>
            <li>
              Fine-grained tool streaming is off by default behind any custom base URL,
              including this one -- Claude Code's own behavior, not something Blindfold
              broke.
            </li>
            <li>
              Optional: set <code>CLAUDE_CODE_ATTRIBUTION_HEADER=0</code> to remove Claude
              Code's attribution block from the system prompt outright. Blindfold's own
              system-block handling preserves the upstream's positional strip of it either
              way, so this just removes the dependency on that ordering.
            </li>
          </ul>
        </div>
      </section>

      <section className="bf-connect-card bf-card" data-testid="connect-card-openai-sdk">
        <h2 className="bf-card-title">Generic OpenAI-SDK client</h2>
        <p className="bf-card-subtitle">
          Works today against <code>POST /v1/chat/completions</code>.
        </p>
        <TerminalAlwaysTabs
          testId="connect-openai-sdk"
          terminal={
            <CopyableSnippet code={openAiTerminalSnippet} label="OPENAI_BASE_URL export" />
          }
          always={
            <>
              <p>Add to your shell profile (e.g. <code>~/.zshrc</code>, <code>~/.bashrc</code>):</p>
              <CopyableSnippet code={openAiTerminalSnippet} label="OPENAI_BASE_URL export" />
              <ProxyDependencyWarning />
            </>
          }
        />
      </section>

      <section className="bf-connect-card bf-card" data-testid="connect-card-codex">
        <h2 className="bf-card-title">Codex CLI</h2>
        <p className="bf-connect-not-supported" role="status" data-testid="connect-codex-not-supported">
          Not supported yet.
        </p>
        <p>
          Codex removed <code>wire_api = "chat"</code> in 0.122 (Feb 2026); <code>responses</code>{" "}
          is the only value it accepts now, and Blindfold doesn't implement{" "}
          <code>POST /v1/responses</code> yet. There's no supported way to point Codex at
          Blindfold today. Pinning Codex to an older release isn't a workaround we recommend:
          it asks you to run a deprecated build of your coding tool to use a privacy proxy,
          which is a bad trade on both counts.
        </p>
        <a
          href={CODEX_ISSUE_URL}
          target="_blank"
          rel="noreferrer"
          className="bf-connect-issue-link"
          data-testid="connect-codex-issue-link"
        >
          Track progress in #263 <ExternalLink size={14} aria-hidden="true" />
        </a>
      </section>
    </div>
  );
}
