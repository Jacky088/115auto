import { fmtSize } from '../../api'

export const EXT_VIDEO = new Set(['mkv', 'mp4', 'ts', 'mov', 'avi', 'rmvb', 'm2ts', 'flv', 'wmv', 'webm', 'iso', 'mpg', 'mpeg'])
export const EXT_SUB = new Set(['srt', 'ass', 'ssa', 'vtt', 'sub', 'sup', 'idx'])
export const EXT_IMG = new Set(['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'svg', 'heic', 'avif'])
export const EXT_AUDIO = new Set(['mp3', 'flac', 'wav', 'aac', 'm4a', 'ogg', 'ape', 'wma', 'dts'])
export const EXT_ARCHIVE = new Set(['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz', 'tgz'])

export const fileKind = (name) => {
  const idx = String(name || '').lastIndexOf('.')
  if (idx < 0) return 'doc'
  const ext = name.slice(idx + 1).toLowerCase()
  if (EXT_VIDEO.has(ext)) return 'video'
  if (EXT_SUB.has(ext)) return 'sub'
  if (EXT_IMG.has(ext)) return 'image'
  if (EXT_AUDIO.has(ext)) return 'audio'
  if (EXT_ARCHIVE.has(ext)) return 'archive'
  return 'doc'
}

export const fileKindLabel = (name) => ({
  video: '视频文件',
  sub: '字幕文件',
  image: '图片文件',
  audio: '音频文件',
  archive: '压缩包',
  doc: '文件',
}[fileKind(name)] || '文件')

export const rowIconName = (row) => {
  if (row?.is_dir) return 'Folder'
  return {
    video: 'VideoPlay',
    sub: 'Tickets',
    image: 'Picture',
    audio: 'Headset',
    archive: 'Files',
    doc: 'Document',
  }[fileKind(row?.name)] || 'Document'
}

export const rowIconClass = (row) => {
  if (row?.is_dir) return 'folder-icon'
  return `file-icon file-icon--${fileKind(row?.name)}`
}

export const formatDirStatSize = (st) => {
  if (!st) return '-'
  if (st.size > 0) return fmtSize(st.size)
  if (st.size_text) return st.size_text
  return '0 B'
}
