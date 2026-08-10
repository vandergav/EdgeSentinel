import { createContext, useContext, useEffect, useRef, useState, type FormEvent } from "react";
import {
  CopilotChatConfigurationProvider,
  CopilotKitCoreRuntimeConnectionStatus,
  useCopilotKit,
} from "@copilotkit/react-core/v2";
import {
  CopilotChat,
  type InputProps,
  type MessagesProps,
  useChatContext,
} from "@copilotkit/react-ui";
import { COPILOT_RUNTIME_ORIGIN } from "../lib/config";

const WELCOME_MESSAGE =
  "Hi, I'm the EdgeOne Agent Team. Ask me to check a site's traffic, review " +
  "your WAF config, or diagnose why something's slow.";

interface AgentTraceEvent {
  id: number;
  at: string;
  runId: string;
  threadId: string;
  type: string;
  title: string;
  status: "started" | "streaming" | "completed" | "failed" | "info";
  detail?: unknown;
}

const ChatTraceScopeContext = createContext<string | null>(null);

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" ? value as Record<string, unknown> : null;
}

function redactDisplayValue(value: unknown, key = ""): unknown {
  if (/authorization|cookie|password|secret|token|api[_-]?key/i.test(key)) return "[redacted]";
  if (typeof value === "string") {
    try {
      return JSON.stringify(redactDisplayValue(JSON.parse(value)));
    } catch {
      return value.replace(
        /((?:authorization|cookie|password|secret|token|api[_-]?key)[^:=]{0,40}[:=]\s*)[^,\s}\]]+/gi,
        "$1[redacted]"
      );
    }
  }
  if (Array.isArray(value)) return value.slice(0, 20).map((item) => redactDisplayValue(item));
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .slice(0, 30)
        .map(([entryKey, entryValue]) => [entryKey, redactDisplayValue(entryValue, entryKey)])
    );
  }
  return value;
}

function displayJson(value: unknown): string {
  if (value === undefined || value === null || value === "") return "No public detail";
  try {
    const rendered = typeof value === "string"
      ? String(redactDisplayValue(value))
      : JSON.stringify(redactDisplayValue(value), null, 2);
    return rendered.length > 900 ? `${rendered.slice(0, 900)}…` : rendered;
  } catch {
    return String(value);
  }
}

function InlineToolCalls({ message, messages }: { message: unknown; messages: unknown[] }) {
  const assistant = record(message);
  const toolCalls = Array.isArray(assistant?.toolCalls) ? assistant.toolCalls : [];
  if (toolCalls.length === 0) return null;

  return (
    <div className="chat-tool-calls" aria-label="Agent tool calls">
      {toolCalls.map((toolCall, index) => {
        const call = record(toolCall);
        const functionCall = record(call?.function);
        const toolCallId = typeof call?.id === "string" ? call.id : `tool-${index}`;
        const result = messages
          .map(record)
          .find((candidate) => candidate?.role === "tool" && candidate.toolCallId === toolCallId);
        return (
          <details className="chat-tool-call" key={toolCallId}>
            <summary>
              <span className="chat-tool-call__indicator" />
              <strong>{String(functionCall?.name ?? "EdgeOne tool")}</strong>
              <small>{result ? "completed" : "running"}</small>
            </summary>
            <dl>
              <dt>Arguments</dt>
              <dd><pre>{displayJson(functionCall?.arguments)}</pre></dd>
              <dt>Result</dt>
              <dd><pre>{displayJson(result?.content)}</pre></dd>
            </dl>
          </details>
        );
      })}
    </div>
  );
}

function AgentActivityTimeline() {
  const threadId = useContext(ChatTraceScopeContext);
  const [events, setEvents] = useState<AgentTraceEvent[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    setEvents([]);
    const query = threadId ? `?threadId=${encodeURIComponent(threadId)}` : "";
    const stream = new EventSource(`${COPILOT_RUNTIME_ORIGIN}/api/agent-trace${query}`);
    stream.addEventListener("trace", (event) => {
      try {
        const traceEvent = JSON.parse((event as MessageEvent<string>).data) as AgentTraceEvent;
        setEvents((current) => {
          // A new run is a new visible execution lifecycle. Keeping only that
          // lifecycle prevents unrelated historical runs from looking like the
          // current chat answer while still retaining every event in the trace
          // service for diagnostics.
          if (traceEvent.type === "RUN_STARTED") return [traceEvent];
          if (current.length > 0 && current[0].runId !== traceEvent.runId) return current;
          return [...current.filter((item) => item.id !== traceEvent.id), traceEvent].slice(-80);
        });
      } catch {
        // A malformed diagnostic event must never interrupt chat.
      }
    });
    stream.onopen = () => setConnected(true);
    stream.onerror = () => setConnected(false);
    return () => stream.close();
  }, [threadId]);

  return (
    <aside className="agent-activity" aria-label="Live agent activity">
      <header>
        <div>
          <h2>Agent activity</h2>
          <p>Live, sanitized AG-UI protocol events</p>
        </div>
        <span className={connected ? "agent-activity__connection agent-activity__connection--live" : "agent-activity__connection"}>
          {connected ? "live" : "reconnecting"}
        </span>
      </header>
      <div className="agent-activity__events" aria-live="polite">
        {events.length === 0 ? (
          <p className="page__subtitle">Send a message to inspect the agent run, tool calls, and state updates.</p>
        ) : events.map((event) => (
          <details className={`agent-activity__event agent-activity__event--${event.status}`} key={event.id}>
            <summary>
              <span className="agent-activity__dot" />
              <div>
                <strong>{event.title}</strong>
                <small>{new Date(event.at).toLocaleTimeString()} · {event.type.replaceAll("_", " ")}</small>
              </div>
            </summary>
            {event.detail !== undefined && <pre>{displayJson(event.detail)}</pre>}
          </details>
        ))}
      </div>
    </aside>
  );
}

/**
 * CopilotKit's built-in Messages and Input components each call
 * useCopilotChatInternal(). The top-level CopilotChat already makes that call,
 * so using all three starts multiple concurrent agent/connect requests whenever
 * this route mounts. These display-only replacements keep one connection owner.
 */
function ChatMessages({
  messages,
  inProgress,
  children,
  RenderMessage,
  AssistantMessage,
  UserMessage,
  ImageRenderer,
  onRegenerate,
  onCopy,
  onThumbsUp,
  onThumbsDown,
  messageFeedback,
  markdownTagRenderers,
  ErrorMessage,
  chatError,
}: MessagesProps) {
  return (
    <div className="copilotKitMessages" aria-live="polite">
      <div className="copilotKitMessagesContainer">
        <div className="chat-welcome-message">{WELCOME_MESSAGE}</div>

        {messages.map((message, index) => (
          <div key={message.id}>
            <RenderMessage
              message={message}
              messages={messages}
              inProgress={inProgress}
              index={index}
              isCurrentMessage={index === messages.length - 1}
              AssistantMessage={AssistantMessage}
              UserMessage={UserMessage}
              ImageRenderer={ImageRenderer}
              onRegenerate={onRegenerate}
              onCopy={onCopy}
              onThumbsUp={onThumbsUp}
              onThumbsDown={onThumbsDown}
              messageFeedback={messageFeedback}
              markdownTagRenderers={markdownTagRenderers}
            />
            <InlineToolCalls message={message} messages={messages} />
          </div>
        ))}

        {chatError && ErrorMessage && <ErrorMessage error={chatError} isCurrentMessage />}
      </div>
      <footer className="copilotKitMessagesFooter">{children}</footer>
    </div>
  );
}

function ChatInput({ inProgress, onSend, onStop, hideStopButton, chatReady = false }: InputProps) {
  const { icons } = useChatContext();
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    // Reset before reading scrollHeight so the input can also shrink after
    // deleting a line. The CSS cap keeps very long drafts scrollable.
    textarea.style.height = "auto";
    textarea.style.height = `${textarea.scrollHeight}px`;
  }, [text]);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const message = text.trim();
    if (!message || inProgress || !chatReady) return;

    setText("");
    void onSend(message);
  };

  return (
    <form className="copilotKitInputContainer" onSubmit={submit}>
      <div className="copilotKitInput">
        <textarea
          ref={textareaRef}
          className="copilotKitInputTextarea"
          data-testid="copilot-chat-textarea"
          placeholder="Ask about your EdgeOne configuration..."
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          disabled={inProgress}
          rows={1}
        />
        <div className="copilotKitInputControls">
          <div style={{ flexGrow: 1 }} />
          {inProgress && !hideStopButton ? (
            <button
              type="button"
              onClick={onStop}
              className="copilotKitInputControlButton"
              aria-label="Stop generating"
            >
              {icons.stopIcon}
            </button>
          ) : (
            <button
              type="submit"
              className="copilotKitInputControlButton"
              disabled={!chatReady || !text.trim()}
              aria-label="Send message"
            >
              {chatReady ? icons.sendIcon : icons.spinnerIcon}
            </button>
          )}
        </div>
      </div>
    </form>
  );
}

export function ChatPage() {
  const { copilotkit } = useCopilotKit();
  const runtimeReady =
    copilotkit.runtimeConnectionStatus === CopilotKitCoreRuntimeConnectionStatus.Connected;
  // One explicit ID ties CopilotKit's AG-UI run and the observability stream
  // to this mounted chat, rather than showing events from another browser.
  const [threadId] = useState(() => globalThis.crypto?.randomUUID?.() ?? `chat-${Date.now()}`);

  return (
    <div className="page page--chat">
      <header className="page__header">
        <h1>Chat</h1>
        <p className="page__subtitle">
          Talk to the EdgeOne Agent Team directly — the orchestrator routes your
          request to the right specialist across all 25 API categories.
        </p>
      </header>

      <CopilotChatConfigurationProvider threadId={threadId} hasExplicitThreadId>
        <ChatTraceScopeContext.Provider value={threadId}>
          <div className="chat-observability-layout">
            <div className="chat-panel">
              {runtimeReady ? (
                <CopilotChat
                  Messages={ChatMessages}
                  Input={ChatInput}
                  labels={{
                    title: "EdgeOne CDN/WAF Engineer",
                  }}
                />
              ) : (
                <div className="chat-welcome-message">Connecting to the EdgeOne agent team…</div>
              )}
            </div>
            <AgentActivityTimeline />
          </div>
        </ChatTraceScopeContext.Provider>
      </CopilotChatConfigurationProvider>
    </div>
  );
}
