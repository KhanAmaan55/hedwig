/**
 * The three-vrm boundary: loading a model, reading its licence, and adapting it to `RigTarget`.
 *
 * The only module in the avatar pane besides `VrmAvatar.tsx` that knows three-vrm exists. Swapping
 * the renderer means replacing these two files; the animator, idle motion, springs and the
 * profile do not change (ADR-0021).
 */

import { VRMLoaderPlugin, VRMUtils, type VRM, type VRMHumanBoneName } from "@pixiv/three-vrm";
import type { Object3D } from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

import type { CharacterProfile } from "./profile";
import type { RigTarget } from "./rig";

/** What a model says about itself. Shown in the pane: the VRM specification expects applications
 * to respect and surface the licence a model carries (docs/28 §5.1). */
export interface ModelInfo {
  readonly name: string;
  readonly authors: string;
  readonly license: string;
  readonly licenseUrl: string | null;
  readonly version: "0" | "1";
}

export type Fetched =
  | { readonly kind: "ok"; readonly bytes: ArrayBuffer }
  | { readonly kind: "missing"; readonly reason: string };

const GLTF_MAGIC = 0x46546c67; // "glTF", little-endian

/**
 * Fetch a model and confirm it is binary glTF before parsing.
 *
 * Checked by magic number, not by status alone: a dev server answers a missing file with its
 * `index.html` and a 200, and parsing HTML as a model produces an error nobody can act on. A
 * missing model is a normal state — a fresh clone has none — not an error.
 */
export async function fetchModel(url: string, signal?: AbortSignal): Promise<Fetched> {
  let response: Response;
  try {
    response = await fetch(url, signal ? { signal } : {});
  } catch (error) {
    if (signal?.aborted) throw error;
    return { kind: "missing", reason: `could not read ${url}` };
  }
  if (!response.ok) return { kind: "missing", reason: `nothing at ${url} (${response.status})` };

  const bytes = await response.arrayBuffer();
  const magic = bytes.byteLength >= 4 ? new DataView(bytes).getUint32(0, true) : 0;
  if (magic !== GLTF_MAGIC) return { kind: "missing", reason: `${url} is not a VRM file` };
  return { kind: "ok", bytes };
}

export async function parseVrm(bytes: ArrayBuffer): Promise<VRM> {
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  const gltf = await loader.parseAsync(bytes, "");
  const vrm = gltf.userData.vrm as VRM | undefined;
  if (!vrm) throw new Error("this file is glTF but not VRM — it has no humanoid or expressions");

  // three-vrm's recommended clean-up: fewer vertices to skin, one skeleton instead of many.
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.combineSkeletons(gltf.scene);
  // VRM 0.x models face the other way; this makes every model face the camera.
  VRMUtils.rotateVRM0(vrm);
  // Skinned meshes are culled by their bind-pose bounds, which can wrongly hide a moved head.
  vrm.scene.traverse((object) => {
    object.frustumCulled = false;
  });
  return vrm;
}

export function modelInfo(vrm: VRM): ModelInfo {
  const meta = vrm.meta;
  if (meta.metaVersion === "1") {
    const terms = [
      meta.avatarPermission ? `avatar use: ${meta.avatarPermission}` : null,
      meta.commercialUsage ? `commercial: ${meta.commercialUsage}` : null,
    ].filter(Boolean);
    return {
      name: meta.name || "Untitled model",
      authors: meta.authors.join(", ") || "unknown author",
      license: ["VRM Public License", ...terms].join(" · "),
      licenseUrl: meta.licenseUrl || null,
      version: "1",
    };
  }
  return {
    name: meta.title || "Untitled model",
    authors: meta.author || "unknown author",
    license: meta.licenseName ?? "unspecified",
    licenseUrl: meta.otherLicenseUrl || null,
    version: "0",
  };
}

/** Bring the model out of its T-pose, once, using the profile's rest pose. */
export function applyRestPose(vrm: VRM, profile: CharacterProfile): void {
  for (const [bone, [x = 0, y = 0, z = 0]] of Object.entries(profile.rest_pose)) {
    vrm.humanoid.getNormalizedBoneNode(bone as VRMHumanBoneName)?.rotation.set(x, y, z);
  }
}

/**
 * `RigTarget` over a VRM.
 *
 * `gaze` is an object the eyes follow — placed in front of the camera, so "looking at the user"
 * means looking at the viewer, and a saccade is an offset from there.
 */
export function vrmRig(vrm: VRM, gaze: Object3D): RigTarget {
  const available = new Set(vrm.expressionManager?.expressions.map((e) => e.expressionName) ?? []);
  const head = vrm.humanoid.getNormalizedBoneNode("head");
  const neck = vrm.humanoid.getNormalizedBoneNode("neck");
  const chest =
    vrm.humanoid.getNormalizedBoneNode("upperChest") ?? vrm.humanoid.getNormalizedBoneNode("chest");
  const spine = vrm.humanoid.getNormalizedBoneNode("spine");

  if (vrm.lookAt) {
    vrm.lookAt.target = gaze;
    vrm.lookAt.autoUpdate = true;
  }

  return {
    setExpression(name, weight) {
      // A model that lacks an expression simply doesn't show it, rather than warning per frame.
      if (available.has(name)) vrm.expressionManager?.setValue(name, weight);
    },
    setHead(yaw, pitch, roll) {
      // Shared between neck and head, so the turn reads as the whole head moving, not a hinge.
      neck?.rotation.set(pitch * 0.4, yaw * 0.4, roll * 0.4);
      head?.rotation.set(pitch * 0.6, yaw * 0.6, roll * 0.6);
    },
    setBreath(angle) {
      chest?.rotation.set(-angle, 0, 0);
      spine?.rotation.set(-angle * 0.5, 0, 0);
    },
    setGaze(x, y) {
      gaze.position.set(x, y, 0);
    },
  };
}
