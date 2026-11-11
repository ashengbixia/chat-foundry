export function createDialogBackdrop(close: () => void) { // 仅按下和松开都在弹窗外时关闭，避免选中文字拖出弹窗时误关
  let pressedOutside = false
  function outside(event: MouseEvent): boolean {
    const dialog = event.currentTarget as HTMLDialogElement | null
    if (!dialog || event.target !== dialog) return false
    const bounds = dialog.getBoundingClientRect()
    return event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom
  }
  return {
    pointerdown(event: PointerEvent) { pressedOutside = outside(event) },
    click(event: MouseEvent) { const shouldClose = pressedOutside && outside(event); pressedOutside = false; if (shouldClose) close() },
  }
}
