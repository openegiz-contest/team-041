// The look of the 3D view: «Голограмма» (the default) or «Реалистичный».
// ?style=hologram|realistic in the address chooses it; otherwise the
// visitor's last choice, kept in the browser (when the browser allows it).

const KEY = 'bozshakol.style3d';
const OLD_KEY = 'aktogay.style3d';         // the page's earlier key: read once, then moved
export const STYLES = ['hologram', 'realistic'];

export function initialStyle() {
  const asked = new URLSearchParams(window.location.search).get('style');
  if (STYLES.includes(asked)) return asked;
  try {
    let kept = window.localStorage.getItem(KEY);
    if (kept === null) {
      kept = window.localStorage.getItem(OLD_KEY);
      if (kept !== null) {
        window.localStorage.setItem(KEY, kept);
        window.localStorage.removeItem(OLD_KEY);
      }
    }
    if (STYLES.includes(kept)) return kept;
  } catch { /* storage blocked: the default */ }
  return 'hologram';
}

export function keepStyle(style) {
  try { window.localStorage.setItem(KEY, style); } catch { /* storage blocked: the choice lasts for this page */ }
  // The address follows the choice, beside the view and the selected twin.
  const url = new URL(window.location.href);
  if (style === 'realistic') url.searchParams.set('style', 'realistic'); else url.searchParams.delete('style');
  window.history.replaceState(null, '', url);
}
