import type { AssistantMessage } from "@shared/api";

/**
 * Per-username QA chat continuity (client-side only -- see AssistantChat.tsx).
 * Closing the chat overlay used to drop the whole conversation on unmount;
 * now it's kept here, keyed by username, so reopening the same user's chat
 * picks up where they left off. Switching to a *different* username still
 * starts fresh, since the assistant panel can only be reached from the home
 * screen (TopNav's user picker isn't reachable while the chat overlay is
 * open), and each username has its own separate key here.
 */

const PREFIX = "rapidboxes.assistantHistory.";

// "Saved for some time", not forever -- a shared kiosk accumulating one
// user's chat history indefinitely would eventually feel like it's reading
// stale, unrelated context back at them. 24h roughly matches a single work
// session/day at the lab; easy to change if that's not the right window.
const TTL_MS = 24 * 60 * 60 * 1000;

interface StoredHistory {
  savedAt: number;
  messages: AssistantMessage[];
}

function key(username: string): string {
  return PREFIX + username.trim().toLowerCase();
}

export function loadChatHistory(username: string): AssistantMessage[] {
  try {
    const raw = localStorage.getItem(key(username));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as StoredHistory;
    if (Date.now() - parsed.savedAt > TTL_MS) {
      localStorage.removeItem(key(username));
      return [];
    }
    return Array.isArray(parsed.messages) ? parsed.messages : [];
  } catch {
    return [];
  }
}

export function saveChatHistory(username: string, messages: AssistantMessage[]): void {
  try {
    if (messages.length === 0) {
      localStorage.removeItem(key(username));
      return;
    }
    const payload: StoredHistory = { savedAt: Date.now(), messages };
    localStorage.setItem(key(username), JSON.stringify(payload));
  } catch {
    /* localStorage full/unavailable -- history continuity is a nicety, never worth breaking chat over */
  }
}

export function clearChatHistory(username: string): void {
  try {
    localStorage.removeItem(key(username));
  } catch {
    /* ignore */
  }
}
