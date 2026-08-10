/**
 * Thin AG-UI <-> CopilotKit protocol bridge.
 *
 * This process has zero agent logic. Its only job is translating
 * CopilotKit's frontend runtime protocol into an AG-UI HttpAgent call
 * against the FastAPI backend (backend/edgeone_agents), which remains the
 * single source of truth for everything the agent team can do.
 *
 * Why this exists at all: CopilotKit's free/OSS path connects the browser
 * to a "Copilot Runtime" (this process), not directly to an arbitrary
 * AG-UI backend — direct browser-to-backend wiring (`selfManagedAgents`)
 * is a CopilotKit Enterprise Intelligence feature. This bridge is the
 * free, production-viable way to get there. See:
 * https://docs.copilotkit.ai/google-adk/concepts/oss-vs-enterprise
 */
import "dotenv/config";
import {
  EventType,
  HttpAgent,
  type AbstractAgent,
  type BaseEvent,
  type RunAgentInput,
  type TextMessageContentEvent,
  type TextMessageEndEvent,
  type TextMessageStartEvent,
} from "@ag-ui/client";
import { CopilotRuntime, createCopilotEndpointExpress } from "@copilotkit/runtime/v2";
import cors from "cors";
import express, { type Response } from "express";
import { Observable } from "rxjs";

const PORT = Number(process.env.PORT ?? 3001);
const AGENT_BACKEND_URL = process.env.AGENT_BACKEND_URL ?? "http://localhost:8000/agui";
const AGENT_ID = process.env.AGENT_ID ?? "edgeone_agent";
const ALLOWED_ORIGINS = (process.env.ALLOWED_ORIGINS ?? "http://localhost:5173")
  .split(",")
  .map((origin) => origin.trim())
  .filter(Boolean);

type TraceStatus = "started" | "streaming" | "completed" | "failed" | "info";

interface DemoTraceEvent {
  id: number;
  at: string;
  runId: string;
  threadId: string;
  type: string;
  title: string;
  status: TraceStatus;
  detail?: unknown;
}

const TRACE_LIMIT = 300;
const TRACE_TEXT_PREVIEW_LIMIT = 600;
let traceSequence = 0;
const traceEvents: DemoTraceEvent[] = [];
const traceSubscribers = new Map<Response, string | null>();

function redactTraceString(value: string): string {
  try {
    return JSON.stringify(redactTraceValue(JSON.parse(value)));
  } catch {
    return value.replace(
      /((?:authorization|cookie|password|secret|token|api[_-]?key)[^:=]{0,40}[:=]\s*)[^,\s}\]]+/gi,
      "$1[redacted]"
    );
  }
}

function redactTraceValue(value: unknown, key = ""): unknown {
  if (/authorization|cookie|password|secret|token|api[_-]?key/i.test(key)) return "[redacted]";
  if (typeof value === "string") {
    const redacted = redactTraceString(value);
    return redacted.length > 600 ? `${redacted.slice(0, 600)}…` : redacted;
  }
  if (Array.isArray(value)) return value.slice(0, 20).map((item) => redactTraceValue(item));
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .slice(0, 30)
        .map(([entryKey, entryValue]) => [entryKey, redactTraceValue(entryValue, entryKey)])
    );
  }
  return value;
}

function publishTrace(event: Omit<DemoTraceEvent, "id" | "at">): void {
  const traceEvent: DemoTraceEvent = {
    ...event,
    id: ++traceSequence,
    at: new Date().toISOString(),
    detail: redactTraceValue(event.detail),
  };
  traceEvents.push(traceEvent);
  if (traceEvents.length > TRACE_LIMIT) traceEvents.splice(0, traceEvents.length - TRACE_LIMIT);
  const payload = `id: ${traceEvent.id}\nevent: trace\ndata: ${JSON.stringify(traceEvent)}\n\n`;
  traceSubscribers.forEach((threadId, subscriber) => {
    if (threadId === null || threadId === traceEvent.threadId) subscriber.write(payload);
  });
}

function appendTextPreview(previews: Map<string, string>, messageId: string, delta: unknown): void {
  if (typeof delta !== "string" || !delta) return;
  const previous = previews.get(messageId) ?? "";
  if (previous.length >= TRACE_TEXT_PREVIEW_LIMIT) return;
  const remaining = TRACE_TEXT_PREVIEW_LIMIT - previous.length;
  previews.set(messageId, `${previous}${redactTraceString(delta).slice(0, remaining)}`);
}

function traceEventFromAgUi(
  input: RunAgentInput,
  event: BaseEvent,
  toolNames: Map<string, string>,
  textPreviews: Map<string, string>
): Omit<DemoTraceEvent, "id" | "at"> | null {
  const record = event as unknown as Record<string, unknown>;
  const runId = String(record.runId ?? input.runId ?? "unknown-run");
  const threadId = String(record.threadId ?? input.threadId ?? "default");
  const type = String(event.type);
  const toolName = typeof record.toolCallName === "string"
    ? record.toolCallName
    : (typeof record.toolCallId === "string" ? toolNames.get(record.toolCallId) : undefined) ?? "EdgeOne tool";
  const toolCallId = typeof record.toolCallId === "string" ? record.toolCallId : undefined;
  const messageId = typeof record.messageId === "string" ? record.messageId : undefined;

  switch (type) {
    case EventType.RUN_STARTED:
      return { runId, threadId, type, title: "Agent run started", status: "started" };
    case EventType.RUN_FINISHED:
      return { runId, threadId, type, title: "Agent run finished", status: "completed" };
    case EventType.RUN_ERROR:
      return { runId, threadId, type, title: "Agent run failed", status: "failed", detail: record };
    case EventType.TEXT_MESSAGE_START:
      return { runId, threadId, type, title: "Streaming assistant response", status: "streaming", detail: { messageId } };
    case EventType.TEXT_MESSAGE_CONTENT:
      if (messageId) appendTextPreview(textPreviews, messageId, record.delta);
      return null;
    case EventType.TEXT_MESSAGE_END:
      return {
        runId,
        threadId,
        type,
        title: "Assistant response complete",
        status: "completed",
        detail: {
          messageId,
          finalTextPreview: messageId ? textPreviews.get(messageId) || "(no text emitted)" : "(no text emitted)",
        },
      };
    case EventType.TOOL_CALL_START:
      if (toolCallId) toolNames.set(toolCallId, toolName);
      return { runId, threadId, type, title: `Calling ${toolName}`, status: "started", detail: { toolCallId } };
    case EventType.TOOL_CALL_ARGS:
      return { runId, threadId, type, title: `Arguments prepared for ${toolName}`, status: "info", detail: { toolCallId, arguments: record.delta } };
    case EventType.TOOL_CALL_END:
      return { runId, threadId, type, title: `${toolName} dispatched`, status: "streaming", detail: { toolCallId } };
    case EventType.TOOL_CALL_RESULT:
      return { runId, threadId, type, title: `${toolName} completed`, status: "completed", detail: { toolCallId, result: record.content } };
    case EventType.STATE_DELTA:
      return { runId, threadId, type, title: "Agent state updated", status: "info", detail: record.delta };
    case EventType.STATE_SNAPSHOT:
      return { runId, threadId, type, title: "Agent state snapshot received", status: "info", detail: record.snapshot };
    case EventType.CUSTOM:
      return { runId, threadId, type, title: `Agent event: ${String(record.name ?? "custom")}`, status: "info", detail: record.value };
    case EventType.STEP_STARTED:
      return { runId, threadId, type, title: `Agent step started: ${String(record.stepName ?? "unnamed")}`, status: "started" };
    case EventType.STEP_FINISHED:
      return { runId, threadId, type, title: `Agent step finished: ${String(record.stepName ?? "unnamed")}`, status: "completed" };
    default:
      return null;
  }
}

/** Read-only protocol telemetry for the local demo. It records no EdgeOne logic. */
function traceAgentEvents(input: RunAgentInput, next: AbstractAgent): Observable<BaseEvent> {
  return new Observable((subscriber) => {
    const toolNames = new Map<string, string>();
    const textPreviews = new Map<string, string>();
    const subscription = next.run(input).subscribe({
      next(event) {
        const traceEvent = traceEventFromAgUi(input, event, toolNames, textPreviews);
        if (traceEvent) publishTrace(traceEvent);
        subscriber.next(event);
      },
      error(error) {
        publishTrace({
          runId: String(input.runId ?? "unknown-run"),
          threadId: String(input.threadId ?? "default"),
          type: "BRIDGE_ERROR",
          title: "Protocol bridge failed",
          status: "failed",
          detail: error instanceof Error ? error.message : String(error),
        });
        subscriber.error(error);
      },
      complete() {
        textPreviews.clear();
        subscriber.complete();
      },
    });
    return () => subscription.unsubscribe();
  });
}

/**
 * Preserve visible token streaming while suppressing only duplicated protocol
 * message IDs. Semantic text buffering previously hid every streamed token.
 */
function deduplicateAssistantMessages(input: RunAgentInput, next: AbstractAgent): Observable<BaseEvent> {
  return new Observable((subscriber) => {
    const seenMessageIds = new Set<string>();
    const suppressedMessageIds = new Set<string>();

    const subscription = next.run(input).subscribe({
      next(event) {
        if (event.type === EventType.TEXT_MESSAGE_START) {
          const textEvent = event as TextMessageStartEvent;
          if (seenMessageIds.has(textEvent.messageId)) {
            suppressedMessageIds.add(textEvent.messageId);
            return;
          }
          seenMessageIds.add(textEvent.messageId);
          subscriber.next(event);
          return;
        }

        if (event.type === EventType.TEXT_MESSAGE_CONTENT) {
          const textEvent = event as TextMessageContentEvent;
          if (suppressedMessageIds.has(textEvent.messageId)) return;
        }

        if (event.type === EventType.TEXT_MESSAGE_END) {
          const textEvent = event as TextMessageEndEvent;
          if (suppressedMessageIds.delete(textEvent.messageId)) return;
        }

        subscriber.next(event);
      },
      error(error) {
        subscriber.error(error);
      },
      complete() {
        subscriber.complete();
      },
    });

    return () => subscription.unsubscribe();
  });
}

const edgeOneAgent = new HttpAgent({ url: AGENT_BACKEND_URL });
edgeOneAgent.use(traceAgentEvents);
edgeOneAgent.use(deduplicateAssistantMessages);

const runtime = new CopilotRuntime({
  agents: {
    [AGENT_ID]: edgeOneAgent,
  },
});

const app = express();
app.use(cors({ origin: ALLOWED_ORIGINS, credentials: true }));

app.get("/api/agent-trace", (request, response) => {
  response.setHeader("Content-Type", "text/event-stream");
  response.setHeader("Cache-Control", "no-cache, no-transform");
  response.setHeader("Connection", "keep-alive");
  response.flushHeaders();
  const after = Number(request.query.after ?? 0);
  const requestedThreadId = typeof request.query.threadId === "string" && request.query.threadId.trim()
    ? request.query.threadId
    : null;
  traceEvents.filter((event) => event.id > after && (requestedThreadId === null || event.threadId === requestedThreadId)).forEach((event) => {
    response.write(`id: ${event.id}\nevent: trace\ndata: ${JSON.stringify(event)}\n\n`);
  });
  traceSubscribers.set(response, requestedThreadId);
  const heartbeat = setInterval(() => response.write(": keepalive\n\n"), 15_000);
  request.on("close", () => {
    clearInterval(heartbeat);
    traceSubscribers.delete(response);
  });
});

// The React client is configured for CopilotKit's single-endpoint transport:
// POST /api/copilotkit with a `{ method, ... }` envelope (including `info`).
const copilotRouter = createCopilotEndpointExpress({
  runtime,
  basePath: "/api/copilotkit",
  mode: "single-route",
});
app.use(copilotRouter);

app.get("/health", (_req, res) => {
  res.json({ status: "ok", service: "copilot-runtime", agent: AGENT_ID, backend: AGENT_BACKEND_URL });
});

app.listen(PORT, () => {
  console.log(
    `copilot-runtime: bridging "${AGENT_ID}" (${AGENT_BACKEND_URL}) -> http://localhost:${PORT}/api/copilotkit`
  );
});
