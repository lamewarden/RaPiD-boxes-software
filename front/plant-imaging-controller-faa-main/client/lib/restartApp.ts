import { toast } from "sonner";

export async function restartAppToHome(onStateChange?: (restarting: boolean) => void) {
  onStateChange?.(true);

  try {
    const restartController = new AbortController();
    const restartTimeout = window.setTimeout(() => restartController.abort(), 5000);

    const restartRes = await fetch("/api/system/restart-service", {
      method: "POST",
      signal: restartController.signal,
    });
    window.clearTimeout(restartTimeout);

    if (!restartRes.ok) {
      throw new Error(`Restart failed: HTTP ${restartRes.status}`);
    }

    await restartRes.json();
    toast.success("Restarting service...");

    let success = false;
    let attempt = 0;
    let delay = 1500;

    while (attempt < 15 && !success) {
      attempt += 1;
      await new Promise((resolve) => window.setTimeout(resolve, delay));

      try {
        const pollController = new AbortController();
        const pollTimeout = window.setTimeout(() => pollController.abort(), 2000);
        const pollRes = await fetch("/api/system", {
          signal: pollController.signal,
          cache: "no-store",
        });
        window.clearTimeout(pollTimeout);

        if (pollRes.ok) {
          await pollRes.json();
          success = true;
        } else {
          delay = Math.min(delay * 1.2, 3000);
        }
      } catch {
        delay = Math.min(delay * 1.2, 3000);
      }
    }

    if (success) {
      window.setTimeout(() => {
        window.location.replace("/");
      }, 1000);
      return;
    }

    toast.error("Service took too long to restart. Please refresh manually.");
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    console.error("[Close] Error during restart:", msg, e);
    toast.error(`Restart failed: ${msg}`);
  } finally {
    onStateChange?.(false);
  }
}