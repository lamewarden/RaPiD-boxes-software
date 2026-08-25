import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Bot, MessageCirclePlus, RotateCcw, X } from "lucide-react";
import OnScreenKeyboard from "@/components/OnScreenKeyboard";
import { api } from "@/lib/api";
import { getUsername } from "@/lib/session";
import { clearChatHistory, loadChatHistory, saveChatHistory } from "@/lib/assistantHistory";
import type { AssistantMessage, ExperimentProposal } from "@shared/api";

/**
 * Full-screen chat overlay (same "takeover" pattern as ImportConfigMenu, not
 * a centered card -- a chat transcript needs the room). The on-screen
 * keyboard is deliberately not docked permanently: it only appears while
 * actively composing a message (same collapsible pattern as
 * UserSelectMenu's "+ New User"), so the transcript gets the full stage the
 * rest of the time on this small 800x452 kiosk screen.
 *
 * The assistant runs against a remote API (no local model to wake), so this
 * opens straight into the chat -- per-message "Thinking…" is the only
 * loading state needed.
 *
 * The conversation is persisted per-username in localStorage (see
 * lib/assistantHistory.ts, ~24h TTL) so closing this overlay and reopening
 * it doesn't lose the thread -- switching to a *different* username still
 * starts fresh (each has its own key, and the picker isn't reachable while
 * this overlay is open anyway). The header's reset icon clears it explicitly.
 *
 * Safety property this UI must never violate: a returned `proposal` is only
 * ever offered as something to review on the real setup screen (the same
 * `loadedConfig` router-state mechanism TopNav's Import already uses) --
 * this component never calls the start-experiment API itself.
 */
export default function AssistantChat({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const username = getUsername();
  const [messages, setMessages] = useState<AssistantMessage[]>(() => loadChatHistory(username));
  const [proposal, setProposal] = useState<ExperimentProposal | null>(null);
  const [composing, setComposing] = useState(false);
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  // Persisted per-username (see lib/assistantHistory.ts) so closing this
  // overlay and reopening it -- as the same user -- picks the conversation
  // back up instead of starting over every time.
  useEffect(() => {
    saveChatHistory(username, messages);
  }, [username, messages]);

  const startNewChat = () => {
    setMessages([]);
    setProposal(null);
    clearChatHistory(username);
  };

  const send = async (text: string) => {
    setComposing(false);
    const trimmed = text.trim();
    if (!trimmed || sending) return;

    const history = messages;
    setMessages((m) => [...m, { role: "user", content: trimmed }]);
    setProposal(null);
    setSending(true);
    try {
      const res = await api.assistantChat(trimmed, history, getUsername());
      setMessages((m) => [...m, { role: "assistant", content: res.reply }]);
      if (res.proposal) setProposal(res.proposal);
    } catch (e) {
      const msg = (e as Error).message;
      const content = msg.startsWith("503")
        ? "The assistant model isn't running right now — try again later."
        : `Something went wrong: ${msg}`;
      setMessages((m) => [...m, { role: "assistant", content }]);
    } finally {
      setSending(false);
    }
  };

  const reviewProposal = () => {
    if (!proposal) return;
    navigate(proposal.protocol === "growth" ? "/growth" : "/tropism", {
      state: { loadedConfig: proposal.config },
    });
  };

  if (composing) {
    return (
      <OnScreenKeyboard
        title="Ask the assistant"
        initialValue=""
        onCancel={() => setComposing(false)}
        onConfirm={send}
      />
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-app-bg-primary">
      <div className="flex items-center justify-between border-b border-app-border-primary bg-app-bg-secondary px-3 py-2">
        <span className="flex items-center gap-1.5 text-[15px] font-bold uppercase tracking-wide text-white">
          <Bot className="h-[18px] w-[18px] text-app-violet-light" strokeWidth={1.5} />
          QA Assistant
        </span>
        <div className="flex items-center gap-1">
          {messages.length > 0 && (
            <button
              onClick={startNewChat}
              disabled={sending}
              title="Start a new chat (clears this saved conversation)"
              className="rounded-md p-1.5 text-app-text-secondary transition-colors hover:bg-app-bg-tertiary hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RotateCcw className="h-[18px] w-[18px]" strokeWidth={1.5} />
            </button>
          )}
          <button
            onClick={onClose}
            className="rounded-md p-1.5 text-app-text-secondary transition-colors hover:bg-app-bg-tertiary hover:text-white"
          >
            <X className="h-[18px] w-[18px]" strokeWidth={1.5} />
          </button>
        </div>
      </div>

      <div ref={scrollRef} className="flex flex-1 flex-col gap-2 overflow-y-auto p-2">
        {messages.length === 0 && !sending && (
          <p className="mt-8 text-center text-[12px] text-app-text-muted">
            Ask how something works, or say "start an experiment like yesterday's" to reuse a
            past run.
          </p>
        )}

        {messages.map((m, i) => (
          <div
            key={i}
            className={`max-w-[85%] whitespace-pre-wrap rounded-[10px] px-2.5 py-1.5 text-[12px] leading-snug ${
              m.role === "user"
                ? "self-end bg-app-violet/25 text-white"
                : "self-start bg-app-bg-secondary text-app-text-secondary"
            }`}
          >
            {m.content}
          </div>
        ))}

        {sending && (
          <div className="self-start rounded-[10px] bg-app-bg-secondary px-2.5 py-1.5 text-[12px] text-app-text-muted">
            Thinking…
          </div>
        )}

        {proposal && (
          <div className="mt-1 flex flex-col gap-1.5 rounded-[10px] border border-app-violet/50 bg-app-violet/10 p-2.5">
            <div className="text-[10px] font-bold uppercase tracking-wide text-app-violet-light">
              Proposed experiment
            </div>
            <div className="text-[11px] text-app-text-secondary">
              From <span className="text-white">{proposal.experimentId}</span> (
              {proposal.sourceUsername})
            </div>
            <div className="flex gap-1.5">
              <button
                onClick={reviewProposal}
                className="flex-1 rounded-md bg-app-violet px-2 py-1.5 text-[11px] font-bold text-white transition-colors hover:bg-app-violet-light"
              >
                Review on Setup Screen
              </button>
              <button
                onClick={() => setProposal(null)}
                className="rounded-md bg-app-bg-tertiary px-2 py-1.5 text-[11px] text-app-text-secondary transition-colors hover:bg-app-border-primary"
              >
                Dismiss
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="border-t border-app-border-primary p-2">
        <button
          onClick={() => setComposing(true)}
          disabled={sending}
          className="flex w-full items-center justify-center gap-2 rounded-[10px] bg-app-violet px-4 py-2.5 text-white transition-colors hover:bg-app-violet-light disabled:cursor-not-allowed disabled:opacity-50"
        >
          <MessageCirclePlus className="h-[16px] w-[16px]" strokeWidth={1.5} />
          <span className="text-[12px] font-black uppercase tracking-[1px]">
            {messages.length === 0 ? "Ask a question" : "Reply"}
          </span>
        </button>
      </div>
    </div>
  );
}
