/** Persisted researcher/experiment identity, set via the on-screen keyboard. */

const USER_KEY = "rapidboxes.username";
const EXP_KEY = "rapidboxes.experimentName";

export function getUsername(): string {
  return localStorage.getItem(USER_KEY) || "pi";
}

export function setUsername(value: string): void {
  localStorage.setItem(USER_KEY, value.trim() || "pi");
}

export function getExperimentName(): string {
  return localStorage.getItem(EXP_KEY) || "experiment";
}

export function setExperimentName(value: string): void {
  localStorage.setItem(EXP_KEY, value.trim() || "experiment");
}
