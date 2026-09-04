export function observeUserActivity(
  callback: () => void,
  options: { pointer?: boolean } = {},
) {
  const pointer = options.pointer !== false;
  if (pointer) document.addEventListener("pointerdown", callback, true);
  document.addEventListener("keydown", callback, true);
  document.addEventListener("wheel", callback, {
    capture: true,
    passive: true,
  });
  return () => {
    if (pointer) document.removeEventListener("pointerdown", callback, true);
    document.removeEventListener("keydown", callback, true);
    document.removeEventListener("wheel", callback, true);
  };
}
