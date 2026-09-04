export function observeBossPage(callback: () => void) {
  let timer = 0;
  const notify = () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(callback, 350);
  };
  const observer = new MutationObserver(notify);
  if (document.body)
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      characterData: true,
    });
  window.addEventListener("popstate", notify);
  window.addEventListener("hashchange", notify);
  return () => {
    observer.disconnect();
    window.clearTimeout(timer);
    window.removeEventListener("popstate", notify);
    window.removeEventListener("hashchange", notify);
  };
}
