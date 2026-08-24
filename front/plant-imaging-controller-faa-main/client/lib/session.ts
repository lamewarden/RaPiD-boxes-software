/** Persisted user/experiment identity, set via the on-screen keyboard. */

const USER_KEY = "rapidboxes.username";
const EXP_KEY = "rapidboxes.experimentName";

export function getUsername(): string {
  return localStorage.getItem(USER_KEY) || "pi";
}

/** Lower-cased on the way in so "Ivan"/"IVAN"/"ivan" always land on the same
 *  working folder -- one identity per person, no separate "already exists"
 *  warning needed since there's nothing to collide with. */
export function setUsername(value: string): void {
  localStorage.setItem(USER_KEY, value.trim().toLowerCase() || "pi");
}

export function getExperimentName(): string {
  return localStorage.getItem(EXP_KEY) || "experiment";
}

export function setExperimentName(value: string): void {
  localStorage.setItem(EXP_KEY, value.trim() || "experiment");
}
