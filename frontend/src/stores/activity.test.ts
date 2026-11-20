import { afterEach, describe, expect, it, vi } from "vitest";

import { activityStore } from "./activity";

afterEach(() => {
  activityStore.set("idle");
});

describe("activityStore", () => {
  it("notifies on change, and only on change", () => {
    const listener = vi.fn();
    const unsubscribe = activityStore.subscribe(listener);
    activityStore.set("thinking");
    activityStore.set("thinking");
    activityStore.set("speaking");
    unsubscribe();
    activityStore.set("idle");
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("opens the mouth only while speaking", () => {
    activityStore.set("speaking");
    activityStore.setMouth(0.7);
    expect(activityStore.mouth()).toBe(0.7);
    activityStore.set("thinking");
    expect(activityStore.mouth()).toBe(0);
  });
});
