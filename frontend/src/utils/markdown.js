import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/core'

import python from 'highlight.js/lib/languages/python'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import json from 'highlight.js/lib/languages/json'
import bash from 'highlight.js/lib/languages/bash'
import sql from 'highlight.js/lib/languages/sql'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'

hljs.registerLanguage('python', python)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('json', json)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('sh', bash)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('html', xml)
hljs.registerLanguage('css', css)

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  highlight: (str, lang) => {
    if (lang && hljs.getLanguage(lang)) {
      try {
        const out = hljs.highlight(str, { language: lang, ignoreIllegals: true }).value
        return `<pre class="hljs"><code>${out}</code></pre>`
      } catch {
        // fall through to plain escape
      }
    }
    return `<pre class="hljs"><code>${md.utils.escapeHtml(str)}</code></pre>`
  },
})

const defaultLinkOpen =
  md.renderer.rules.link_open ||
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))

md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  const t = tokens[idx]
  t.attrSet('target', '_blank')
  t.attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, idx, options, env, self)
}

function escapeHtml(value) {
  return md.utils.escapeHtml(String(value ?? ''))
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/"/g, '&quot;')
}

function urlKeys(url) {
  const raw = String(url || '').trim()
  if (!raw) return []
  try {
    const parsed = new URL(raw)
    parsed.hostname = parsed.hostname.toLowerCase()
    const keys = new Set()
    const add = (u) => {
      keys.add(u.href)
      if (u.href.endsWith('/')) keys.add(u.href.slice(0, -1))
      const protocolSwap = new URL(u.href)
      protocolSwap.protocol = protocolSwap.protocol === 'https:' ? 'http:' : 'https:'
      keys.add(protocolSwap.href)
      if (protocolSwap.href.endsWith('/')) keys.add(protocolSwap.href.slice(0, -1))
    }
    add(parsed)
    const withoutHash = new URL(parsed.href)
    withoutHash.hash = ''
    add(withoutHash)
    const withoutQuery = new URL(withoutHash.href)
    withoutQuery.search = ''
    add(withoutQuery)
    return [...keys]
  } catch {
    return raw ? [raw] : []
  }
}

function addFavicon(favicons, url, favicon) {
  if (!url || !favicon) return
  for (const key of urlKeys(url)) {
    favicons.set(key, String(favicon).trim())
  }
}

function findFavicon(favicons, url) {
  for (const key of urlKeys(url)) {
    const favicon = favicons.get(key)
    if (favicon) return favicon
  }
  return ''
}

function walkFavicons(value, favicons) {
  if (Array.isArray(value)) {
    value.forEach(item => walkFavicons(item, favicons))
    return
  }
  if (!value || typeof value !== 'object') return
  if (typeof value.url === 'string' && typeof value.favicon === 'string') {
    addFavicon(favicons, value.url, value.favicon)
  }
  Object.values(value).forEach(item => walkFavicons(item, favicons))
}

function decodeJsonString(value) {
  try {
    return JSON.parse(`"${value.replace(/"/g, '\\"')}"`)
  } catch {
    return value
  }
}

function scanFavicons(text, favicons) {
  const patterns = [
    /"url"\s*:\s*"([^"]+)"[\s\S]{0,500}?"favicon"\s*:\s*"([^"]+)"/g,
    /"favicon"\s*:\s*"([^"]+)"[\s\S]{0,500}?"url"\s*:\s*"([^"]+)"/g,
  ]
  for (const pattern of patterns) {
    for (const match of String(text || '').matchAll(pattern)) {
      const first = decodeJsonString(match[1])
      const second = decodeJsonString(match[2])
      if (pattern.source.startsWith('"url"')) addFavicon(favicons, first, second)
      else addFavicon(favicons, second, first)
    }
  }
}

export function collectFaviconsFromToolResults(tools = []) {
  const favicons = new Map()
  for (const tool of tools || []) {
    for (const item of [...(tool?.sourceFavicons || []), ...(tool?.source_favicons || [])]) {
      addFavicon(favicons, item?.url, item?.favicon)
    }
    const result = typeof tool?.result === 'string' ? tool.result : ''
    scanFavicons(result, favicons)
    const start = result.indexOf('{')
    const end = result.lastIndexOf('}')
    if (start < 0 || end <= start) continue
    try {
      walkFavicons(JSON.parse(result.slice(start, end + 1)), favicons)
    } catch {
      // Truncated tool output is still handled by scanFavicons above.
    }
  }
  return favicons
}

function cleanTitle(value) {
  return String(value || '')
    .replace(/^\s*(?:[-*]|\d+[.)、:：]|\[\d+]|\【\d+】)\s*/, '')
    .replace(/\[[^\]]+]\([^)]+\)/g, '')
    .replace(/<https?:\/\/[^>]+>/g, '')
    .replace(/https?:\/\/[^\s)\]>，。；;]+/g, '')
    .replace(/[*_`~]/g, '')
    .replace(/^[\s:：\-—–,，.。]+|[\s:：\-—–,，.。]+$/g, '')
    .trim()
}

function parseReference(raw, id, faviconsByUrl = new Map()) {
  const text = String(raw || '').trim().replace(/^\s*(?:\[\d+]|\【\d+】)\s*/, '')
  const link = text.match(/\[([^\]]+)]\((https?:\/\/[^)\s]+)(?:\s+"[^"]*")?\)/)
  const htmlLink = text.match(/<a\s+[^>]*href=["'](https?:\/\/[^"']+)["'][^>]*>(.*?)<\/a>/i)
  const angleUrl = text.match(/<((?:https?:\/\/)[^>\s]+)>/)
  const looseUrl = text.match(/https?:\/\/[^\s)\]>，。；;]+/)
  const url = link ? link[2] : (htmlLink ? htmlLink[1] : (angleUrl ? angleUrl[1] : (looseUrl ? looseUrl[0] : text)))
  let parsed = null
  try {
    parsed = new URL(url)
  } catch {
    // Keep the original text visible even when the footnote is not a valid URL.
  }
  const isWebUrl = parsed && ['http:', 'https:'].includes(parsed.protocol)
  const htmlTitle = htmlLink ? htmlLink[2].replace(/<[^>]+>/g, '') : ''
  const looseTitle = cleanTitle(text)
  const title = (link?.[1] || htmlTitle || looseTitle || parsed?.hostname || text || `引用 ${id}`).trim()
  const href = isWebUrl ? parsed.href : ''
  const mappedFavicon = href ? findFavicon(faviconsByUrl, href) : ''
  return {
    id,
    title,
    url: href,
    host: parsed?.hostname.replace(/^www\./, '') || '',
    favicon: mappedFavicon || (isWebUrl ? `${parsed.protocol}//${parsed.hostname}/favicon.ico` : ''),
  }
}

function parseReferenceListItem(line) {
  const match = line.match(/^\s*(?:[-*]\s+|\d+[.)、:：]\s*|\[\d+]\s*|\【\d+】\s*)(.+?)\s*$/)
  return match?.[1] || ''
}

function isReferenceHeading(line) {
  const normalized = String(line || '')
    .replace(/^#{1,6}\s*/, '')
    .replace(/[*_`~]/g, '')
    .trim()
  return /^(参考资料|参考引用|引用来源|资料来源|来源|References|Sources)\s*[:：]?\s*$/i.test(normalized)
}

function extractFootnotes(text, faviconsByUrl = new Map()) {
  const lines = String(text).split(/\r?\n/)
  const body = []
  const notes = []
  let inFence = false
  let inReferenceSection = false

  for (const line of lines) {
    if (/^\s*(```|~~~)/.test(line)) {
      inFence = !inFence
      body.push(line)
      continue
    }
    if (!inFence) {
      const match = line.match(/^\[\^([^\]]+)]:\s*(.+?)\s*$/)
      if (match) {
        notes.push(parseReference(match[2], match[1], faviconsByUrl))
        continue
      }
      if (isReferenceHeading(line)) {
        inReferenceSection = true
        continue
      }
      if (inReferenceSection) {
        const item = parseReferenceListItem(line)
        if (item) {
          notes.push(parseReference(item, String(notes.length + 1), faviconsByUrl))
          continue
        }
        if (!line.trim()) continue
        inReferenceSection = false
      }
    }
    body.push(line)
  }

  return { body: body.join('\n').trimEnd(), notes }
}

function markFootnoteRefs(text, noteIds) {
  if (!noteIds.size) return text
  const lines = String(text).split(/\r?\n/)
  let inFence = false
  return lines.map(line => {
    if (/^\s*(```|~~~)/.test(line)) {
      inFence = !inFence
      return line
    }
    if (inFence) return line
    return line.replace(/\[\^([^\]]+)]/g, (full, id) =>
      noteIds.has(id) ? `@@FNREF-${encodeURIComponent(id)}@@` : full
    )
  }).join('\n')
}

function linkFootnoteRefs(html, noteIds) {
  const refCounts = new Map()
  return html.replace(/@@FNREF-([^@]+)@@/g, (full, encodedId) => {
    const id = decodeURIComponent(encodedId)
    if (!noteIds.has(id)) return full
    const nextCount = (refCounts.get(id) || 0) + 1
    refCounts.set(id, nextCount)
    const safeId = encodeURIComponent(id)
    const refId = `fnref-${safeId}-${nextCount}`
    return `<sup class="md-footnote-ref" id="${refId}"><a href="#fn-${safeId}">${escapeHtml(id)}</a></sup>`
  })
}

function fallbackIconHtml() {
  return '<span class="md-reference-fallback" aria-hidden="true"></span>'
}

function renderReferences(notes) {
  if (!notes.length) return ''
  const items = notes.map((note, index) => {
    const safeId = encodeURIComponent(note.id)
    const icon = note.favicon
      ? `<span class="md-reference-icon">${fallbackIconHtml()}<img class="md-reference-favicon" src="${escapeAttr(note.favicon)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none';this.previousElementSibling.style.display='inline-flex'" /></span>`
      : `<span class="md-reference-icon">${fallbackIconHtml()}</span>`
    const title = note.url
      ? `<a class="md-reference-title" href="${escapeAttr(note.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(note.title)}</a>`
      : `<span class="md-reference-title">${escapeHtml(note.title)}</span>`
    const host = note.host ? `<span class="md-reference-host">${escapeHtml(note.host)}</span>` : ''
    return `<li class="md-reference-item" id="fn-${safeId}">
      <span class="md-reference-index">${escapeHtml(note.id || index + 1)}</span>
      ${icon}
      <span class="md-reference-text">
        ${title}
        ${host}
      </span>
      <a class="md-reference-back" href="#fnref-${safeId}-1" aria-label="返回引用">↩</a>
    </li>`
  }).join('')
  return `<section class="md-references" aria-label="参考引用">
    <div class="md-references-title">参考资料</div>
    <ol class="md-references-list">${items}</ol>
  </section>`
}

export function renderMarkdown(text, options = {}) {
  if (!text) return ''
  const faviconsByUrl = options.faviconsByUrl || new Map()
  const { body, notes } = extractFootnotes(text, faviconsByUrl)
  const noteIds = new Set(notes.map(note => note.id))
  const renderedBody = linkFootnoteRefs(md.render(markFootnoteRefs(body, noteIds)), noteIds)
  return renderedBody + renderReferences(notes)
}
