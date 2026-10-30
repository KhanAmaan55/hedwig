import type { VRM } from "@pixiv/three-vrm";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchModel, modelInfo } from "./vrm";

function respond(body: ArrayBuffer | string, status = 200): void {
  const bytes = typeof body === "string" ? new TextEncoder().encode(body).buffer : body;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      arrayBuffer: () => Promise.resolve(bytes),
    }),
  );
}

function glb(): ArrayBuffer {
  const buffer = new ArrayBuffer(12);
  new DataView(buffer).setUint32(0, 0x46546c67, true); // "glTF"
  return buffer;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchModel", () => {
  it("accepts binary glTF", async () => {
    respond(glb());
    expect((await fetchModel("avatars/hedwig.vrm")).kind).toBe("ok");
  });

  it("treats a dev server's index.html fallback as no model, not as a broken one", async () => {
    // A missing public file is answered with index.html and a 200 — parsing that as a model
    // would produce an error nobody could act on.
    respond("<!doctype html><html></html>");
    const result = await fetchModel("avatars/hedwig.vrm");
    expect(result).toEqual({ kind: "missing", reason: "avatars/hedwig.vrm is not a VRM file" });
  });

  it("treats a 404 as no model", async () => {
    respond("", 404);
    expect((await fetchModel("avatars/hedwig.vrm")).kind).toBe("missing");
  });

  it("treats an unreadable location as no model", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network")));
    expect((await fetchModel("avatars/hedwig.vrm")).kind).toBe("missing");
  });
});

describe("modelInfo — the licence a model carries", () => {
  it("reads a VRM 1.0 model's name, authors and terms", () => {
    const vrm = {
      meta: {
        metaVersion: "1",
        name: "Sample",
        authors: ["pixiv"],
        licenseUrl: "https://vrm.dev/licenses/1.0/",
        avatarPermission: "everyone",
        commercialUsage: "personalNonProfit",
      },
    } as unknown as VRM;

    expect(modelInfo(vrm)).toEqual({
      name: "Sample",
      authors: "pixiv",
      license: "VRM Public License · avatar use: everyone · commercial: personalNonProfit",
      licenseUrl: "https://vrm.dev/licenses/1.0/",
      version: "1",
    });
  });

  it("reads a VRM 0.x model", () => {
    const vrm = {
      meta: { metaVersion: "0", title: "Old", author: "someone", licenseName: "CC_BY" },
    } as unknown as VRM;

    expect(modelInfo(vrm)).toMatchObject({ name: "Old", authors: "someone", license: "CC_BY" });
  });

  it("says so when a model doesn't name itself", () => {
    const vrm = {
      meta: { metaVersion: "1", name: "", authors: [], licenseUrl: "" },
    } as unknown as VRM;

    expect(modelInfo(vrm)).toMatchObject({
      name: "Untitled model",
      authors: "unknown author",
      licenseUrl: null,
    });
  });
});
