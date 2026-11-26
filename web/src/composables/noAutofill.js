const SKIP_TYPES = new Set(['checkbox', 'radio', 'file', 'hidden', 'submit', 'button', 'reset', 'image'])

function applyToField(el) {
  if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)) return
  if (el.hasAttribute('data-allow-autofill') || el.closest('[data-allow-autofill]')) return
  if (el instanceof HTMLInputElement && SKIP_TYPES.has(el.type)) return

  const want = el instanceof HTMLInputElement && el.type === 'password' ? 'new-password' : 'off'
  if (el.getAttribute('autocomplete') !== want) {
    el.setAttribute('autocomplete', want)
  }
  if (!el.hasAttribute('data-lpignore')) el.setAttribute('data-lpignore', 'true')
  if (!el.hasAttribute('data-1p-ignore')) el.setAttribute('data-1p-ignore', 'true')
  if (!el.hasAttribute('data-bwignore')) el.setAttribute('data-bwignore', 'true')
}

function scanSubtree(root) {
  if (!(root instanceof Element)) return
  applyToField(root)
  root.querySelectorAll('input, textarea').forEach(applyToField)
}

/**
 * 全局禁用非登录表单的浏览器自动填充（仅带 data-allow-autofill 的登录表单保留）。
 */
export function installNoAutofill() {
  if (typeof document === 'undefined' || !document.body) return
  scanSubtree(document.body)
  const obs = new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.type === 'attributes') {
        applyToField(m.target)
      } else {
        m.addedNodes.forEach(scanSubtree)
      }
    }
  })
  obs.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['type'],
  })
}
