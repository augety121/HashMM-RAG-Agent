import { createFeatureContext, type FeatureContextKind } from "./chatContext";
import { useStore } from "./store";

/** Insert a reviewed feature snapshot into the current Chat composer.
 *
 * This does not create another Chat, transfer a run lease, or grant authority.
 * The user can inspect and edit the resulting prompt before sending it.
 */
export function insertContextIntoChat(
  kind: FeatureContextKind,
  title: string,
  value: unknown,
  prompt: string,
  source = "",
): void {
  const state = useStore.getState();
  state.attachFeatureContext(createFeatureContext(kind, title, value, source));
  state.set({ desktopView: null, pendingPrompt: prompt });
}
