/**
 * The VRM renderer (docs/28 §5).
 *
 * Owns a three.js scene and a render loop, and nothing else. It is handed semantic intent and
 * animates toward it; it never reads emotion, and it never decides what an expression *means* —
 * the character profile does (docs/18 §6: "the renderer never reads the mind store").
 *
 * Per-frame work never touches React state. Intent goes into the animator through a ref, and the
 * loop reads it from there (docs/18 §4 rule 3).
 */

import type { VRM } from "@pixiv/three-vrm";
import { VRMUtils } from "@pixiv/three-vrm";
import { useEffect, useRef, useState, type JSX } from "react";
import {
  AmbientLight,
  DirectionalLight,
  Object3D,
  PerspectiveCamera,
  Scene,
  Timer,
  Vector3,
  WebGLRenderer,
} from "three";

import type { ExpressionIntent } from "../../transport/types";

import { AvatarAnimator } from "./animator";
import { DEFAULT_PROFILE, validateProfile, type CharacterProfile } from "./profile";
import type { RigTarget } from "./rig";
import { applyRestPose, fetchModel, modelInfo, parseVrm, vrmRig, type ModelInfo } from "./vrm";

export type AvatarStatus =
  | { readonly state: "loading" }
  | { readonly state: "ready"; readonly model: ModelInfo }
  | { readonly state: "missing"; readonly reason: string }
  | { readonly state: "error"; readonly message: string };

export interface VrmAvatarProps {
  /** URL of a `.vrm` file — a path, or an object URL for a dropped file. */
  readonly src: string;
  /** The emotion API: what to express. `null` until the first reading arrives. */
  readonly expression: ExpressionIntent | null;
  readonly reducedMotion?: boolean;
  readonly profile?: CharacterProfile;
  readonly onStatus?: (status: AvatarStatus) => void;
}

/** Longest step the animation takes in one frame. A tab returning from the background must not
 * fast-forward the springs through seconds of motion in one jump. */
const MAX_DT = 0.1;

interface Stage {
  readonly renderer: WebGLRenderer;
  readonly scene: Scene;
  readonly camera: PerspectiveCamera;
  readonly gaze: Object3D;
  vrm: VRM | null;
  rig: RigTarget | null;
}

export function VrmAvatar({
  src,
  expression,
  reducedMotion = false,
  profile = DEFAULT_PROFILE,
  onStatus,
}: VrmAvatarProps): JSX.Element {
  const host = useRef<HTMLDivElement>(null);
  const stage = useRef<Stage | null>(null);
  // Built once. `useRef(new AvatarAnimator(...))` would construct one on every render and throw
  // it away. The profile is read here, at mount: to change character, remount with a new `key`.
  const [animator] = useState(() => new AvatarAnimator(profile, reducedMotion));
  const report = useRef(onStatus);

  useEffect(() => {
    report.current = onStatus;
  }, [onStatus]);

  // -- the emotion API: new intent goes to the animator, never into React state per frame --
  useEffect(() => {
    if (expression) animator.setIntent(expression);
  }, [animator, expression]);

  useEffect(() => {
    animator.setReducedMotion(reducedMotion);
  }, [animator, reducedMotion]);

  // -- the scene and the loop: created once, torn down completely ------------------------
  useEffect(() => {
    const element = host.current;
    if (!element) return;

    const renderer = new WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    element.appendChild(renderer.domElement);

    const scene = new Scene();
    const camera = new PerspectiveCamera(28, 1, 0.1, 20);
    const gaze = new Object3D();
    camera.add(gaze); // the eyes follow a point at the viewer, offset by saccades
    scene.add(camera);

    const key = new DirectionalLight(0xffffff, Math.PI * 0.9);
    key.position.set(0.6, 1.2, 1.5);
    scene.add(key, new AmbientLight(0xffffff, 0.6));

    stage.current = { renderer, scene, camera, gaze, vrm: null, rig: null };

    const resize = () => {
      const { clientWidth: width, clientHeight: height } = element;
      if (width === 0 || height === 0) return;
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    resize();

    // three's Timer, connected to the document, already ignores time spent in a hidden tab.
    const timer = new Timer();
    timer.connect(document);
    let frame = 0;
    const loop = (now: number) => {
      frame = requestAnimationFrame(loop);
      timer.update(now);
      const dt = Math.min(timer.getDelta(), MAX_DT);
      const current = stage.current;
      if (current?.vrm && current.rig) {
        animator.step(dt, current.rig);
        current.vrm.update(dt);
      }
      renderer.render(scene, camera);
    };

    // An avatar animating for nobody is a CPU cost with no value (docs/15 §8).
    const visibility = () => {
      cancelAnimationFrame(frame);
      if (!document.hidden) frame = requestAnimationFrame(loop);
    };
    document.addEventListener("visibilitychange", visibility);
    frame = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("visibilitychange", visibility);
      timer.dispose();
      observer.disconnect();
      const current = stage.current;
      if (current?.vrm) VRMUtils.deepDispose(current.vrm.scene);
      renderer.dispose();
      renderer.domElement.remove();
      stage.current = null;
    };
  }, [animator]);

  // -- the model: loaded whenever the source changes ----------------------------------
  useEffect(() => {
    const controller = new AbortController();
    // Read through a function: a check before an `await` says nothing about after it, and a
    // narrowed property would let the compiler assume it does.
    const aborted = () => controller.signal.aborted;
    report.current?.({ state: "loading" });

    const problems = validateProfile(profile);
    if (problems.length > 0) {
      report.current?.({ state: "error", message: `character profile: ${problems.join("; ")}` });
      return () => {
        controller.abort();
      };
    }

    void (async () => {
      try {
        const fetched = await fetchModel(src, controller.signal);
        if (aborted()) return;
        if (fetched.kind === "missing") {
          report.current?.({ state: "missing", reason: fetched.reason });
          return;
        }

        const vrm = await parseVrm(fetched.bytes);
        const current = stage.current;
        if (aborted() || !current) {
          VRMUtils.deepDispose(vrm.scene);
          return;
        }

        if (current.vrm) {
          current.scene.remove(current.vrm.scene);
          VRMUtils.deepDispose(current.vrm.scene);
        }
        applyRestPose(vrm, profile);
        current.scene.add(vrm.scene);
        frameHead(vrm, current.camera);
        current.vrm = vrm;
        current.rig = vrmRig(vrm, current.gaze);
        report.current?.({ state: "ready", model: modelInfo(vrm) });
      } catch (error) {
        if (aborted()) return;
        const message = error instanceof Error ? error.message : String(error);
        report.current?.({ state: "error", message });
      }
    })();

    return () => {
      controller.abort();
    };
  }, [src, profile]);

  return <div ref={host} className="avatar__canvas" />;
}

/** A head-and-shoulders shot, whatever the model's height. */
function frameHead(vrm: VRM, camera: PerspectiveCamera): void {
  vrm.update(0);
  vrm.scene.updateMatrixWorld(true);
  const head = vrm.humanoid.getNormalizedBoneNode("head");
  const position = new Vector3(0, 1.4, 0);
  head?.getWorldPosition(position);
  camera.position.set(0, position.y - 0.02, 0.95);
  camera.lookAt(0, position.y - 0.08, 0);
}
