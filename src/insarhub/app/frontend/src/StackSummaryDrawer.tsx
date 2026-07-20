import { useMemo, useState, useRef, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import type { Theme } from './theme'
import { useResizable, ResizeHandle } from './useResizable'

const API = import.meta.env.DEV ? 'http://localhost:8080' : ''

interface StackSummary {
  stackKey:              string
  path:                  number
  frame:                 number
  sceneCount:            number
  startDate:             string
  endDate:               string
  flightDirection:       string
  platform:              string
  representativeFeature: GeoJSON.Feature
}

interface Props {
  footprints:       GeoJSON.FeatureCollection
  theme:            Theme
  selectedStackKey: string | null
  workdir:          string
  aoiWkt:           string | null
  downloaderType:   string
  onStackHover:     (key: string | null) => void
  onStackClick:     (feature: GeoJSON.Feature) => void
  onCheckedChange:  (keys: string[]) => void
  onClose:          () => void
}

function parseStack(key: string): { path: number; frame: number } {
  const m = key.match(/\(?\s*(\d+)\s*,\s*(\d+)\s*\)?/)
  return m ? { path: parseInt(m[1]), frame: parseInt(m[2]) } : { path: 0, frame: 0 }
}

export default function StackSummaryDrawer({
  footprints, theme: t, selectedStackKey, workdir, aoiWkt, downloaderType,
  onStackHover, onStackClick, onCheckedChange, onClose,
}: Props) {
  const { t: tr } = useTranslation()
  const { width, onHandleMouseDown } = useResizable(260)

  const stacks = useMemo<StackSummary[]>(() => {
    const map = new Map<string, StackSummary>()
    // Tracks every distinct platform seen per stack — a satellite handover
    // (e.g. Sentinel-1C → Sentinel-1D on the same track) means one (path,
    // frame) group can legitimately span multiple platforms. Using only the
    // first-seen scene's platform silently mislabels the whole group.
    const platformsByKey = new Map<string, Set<string>>()
    for (const feature of footprints.features) {
      const key = feature.properties?._stack as string | undefined
      if (!key) continue
      if (!map.has(key)) {
        const { path, frame } = parseStack(key)
        map.set(key, {
          stackKey:              key,
          path, frame,
          sceneCount:            0,
          startDate:             '',
          endDate:               '',
          flightDirection:       (feature.properties?.flightDirection as string) ?? '',
          platform:              '',
          representativeFeature: feature,
        })
      }
      const s = map.get(key)!
      s.sceneCount++
      const pf = feature.properties?.platform as string | undefined
      if (pf) {
        let set = platformsByKey.get(key)
        if (!set) { set = new Set(); platformsByKey.set(key, set) }
        set.add(pf)
        s.platform = Array.from(set).sort().join(', ')
      }
      const date = ((feature.properties?.startTime as string) ?? '').slice(0, 10)
      if (date) {
        if (!s.startDate || date < s.startDate) s.startDate = date
        if (!s.endDate   || date > s.endDate)   s.endDate   = date
      }
    }
    return Array.from(map.values()).sort((a, b) => a.path - b.path || a.frame - b.frame)
  }, [footprints])

  // ── Multi-select + trigger state ───────────────────────────────────────────
  const [checked,   setChecked]   = useState<Set<string>>(new Set())
  const [triggered, setTriggered] = useState<Set<string>>(new Set())

  const emitChecked = useCallback((next: Set<string>) => {
    onCheckedChange(Array.from(next))
  }, [onCheckedChange])

  const toggleCheck = (key: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setChecked(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      emitChecked(next)
      return next
    })
  }

  const toggleAll = () => {
    setChecked(prev => {
      const next = prev.size === stacks.length ? new Set<string>() : new Set(stacks.map(s => s.stackKey))
      emitChecked(next)
      return next
    })
  }

  // ── Add Job (merged) ─────────────────────────────────────────────────────────
  const [ajStatus, setAjStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [ajMsg,     setAjMsg]   = useState('')

  const startAddJob = async () => {
    const selectedStacks = stacks.filter(s => checked.has(s.stackKey))
    if (!selectedStacks.length) return
    setAjStatus('running'); setAjMsg('')
    try {
      const res = await fetch(`${API}/api/add-merged-job`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workdir, downloaderType,
          stacks: selectedStacks.map(s => ({
            relativeOrbit:   s.path,
            frame:           s.frame,
            start:           s.startDate,
            end:             s.endDate,
            wkt:             aoiWkt ?? undefined,
            flightDirection: s.flightDirection || undefined,
            // platform intentionally omitted for merges — a (path, frame)
            // group can legitimately span a satellite handover (e.g.
            // Sentinel-1C → Sentinel-1D on the same track); filtering the
            // merged re-search to one platform would silently drop the rest.
          })),
        }),
      })
      const d = await res.json()
      if (!res.ok) { setAjStatus('error'); setAjMsg(d.detail ?? tr('scenePanel.error')); return }
      setAjStatus('done')
      setAjMsg(d.name ?? d.path ?? '')
    } catch (e) {
      setAjStatus('error')
      setAjMsg(String(e))
    }
  }

  // ── Merged download job polling ─────────────────────────────────────────────
  const [_dlJobId, setDlJobId]  = useState<string | null>(null)
  const [dlStatus, setDlStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [dlMsg,    setDlMsg]    = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  const startMergedDownload = async () => {
    const selectedStacks = stacks.filter(s => checked.has(s.stackKey))
    if (!selectedStacks.length) return
    setTriggered(new Set(checked))

    const body = {
      workdir,
      downloaderType,
      download_slc:   true,
      download_orbit: true,
      stacks: selectedStacks.map(s => ({
        relativeOrbit:   s.path,
        frame:           s.frame,
        start:           s.startDate,
        end:             s.endDate,
        wkt:             aoiWkt ?? undefined,
        flightDirection: s.flightDirection || undefined,
        // platform intentionally omitted for merges — see startAddJob for why.
      })),
    }

    try {
      const r   = await fetch(`${API}/api/download-merged`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const { job_id } = await r.json()
      setDlJobId(job_id)
      setDlStatus('running')
      setDlMsg(tr('scenePanel.starting'))

      if (pollRef.current) clearInterval(pollRef.current)
      pollRef.current = setInterval(async () => {
        const res = await fetch(`${API}/api/jobs/${job_id}`)
        const job = await res.json()
        setDlMsg(job.message)
        if (job.status === 'done' || job.status === 'error') {
          clearInterval(pollRef.current!)
          setDlStatus(job.status)
          setDlJobId(null)
        }
      }, 1500)
    } catch (e) {
      setDlStatus('error')
      setDlMsg(String(e))
    }
  }

  const checkedCount = checked.size
  const dlColor = dlStatus === 'done' ? '#4caf50' : dlStatus === 'error' ? '#e53935' : t.accent

  return (
    <div style={{
      position: 'relative', width, height: '100%',
      background: t.bg, borderLeft: `1px solid ${t.border}`,
      display: 'flex', flexDirection: 'column',
    }}>
      <ResizeHandle onMouseDown={onHandleMouseDown} />

      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '8px 12px', borderBottom: `1px solid ${t.border}`,
        background: t.bg2, flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <input
            type="checkbox"
            checked={checkedCount === stacks.length && stacks.length > 0}
            ref={el => { if (el) el.indeterminate = checkedCount > 0 && checkedCount < stacks.length }}
            onChange={toggleAll}
            style={{ accentColor: t.accent, cursor: 'pointer' }}
            title={tr('stackSummary.selectAll')}
          />
          <span style={{ color: t.text, fontWeight: 700, fontSize: 13 }}>
            {tr('stackSummary.stackCount', { count: stacks.length })}
          </span>
        </div>
        <button
          onClick={onClose}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: t.textMuted, fontSize: 18, lineHeight: 1, padding: '0 2px' }}
        >×</button>
      </div>

      {/* Merged download bar — shown when any stack is checked */}
      {checkedCount > 0 && (
        <div style={{
          padding: '8px 12px', borderBottom: `1px solid ${t.border}`,
          background: `${t.accent}11`, flexShrink: 0,
          display: 'flex', flexDirection: 'column', gap: 6,
        }}>
          <button
            onClick={startAddJob}
            disabled={ajStatus === 'running'}
            style={{
              width: '100%', padding: '6px 10px',
              background: ajStatus === 'running' ? t.inputBg
                        : ajStatus === 'done'    ? '#1b3a2a'
                        : ajStatus === 'error'   ? '#b71c1c'
                        : '#0d3b6e',
              color: ajStatus === 'running' ? t.textMuted
                   : ajStatus === 'done'    ? '#a5d6a7'
                   : ajStatus === 'error'   ? '#ef9a9a'
                   : '#90caf9',
              border: `1px solid ${ajStatus === 'done' ? '#2e7d32' : ajStatus === 'error' ? '#c62828' : '#1565c0'}`,
              borderRadius: 4,
              cursor: ajStatus === 'running' ? 'wait' : 'pointer',
              fontWeight: 600, fontSize: 12,
            }}
          >
            {ajStatus === 'running' ? tr('stackSummary.addingJob')
            : ajStatus === 'done'   ? tr('scenePanel.jobAdded')
            : ajStatus === 'error'  ? tr('scenePanel.retry')
            : tr('stackSummary.addJobMerged', { count: checkedCount })}
          </button>
          {ajMsg && (
            <span style={{
              fontSize: 10, wordBreak: 'break-all',
              color: ajStatus === 'error' ? '#e53935' : t.textMuted,
            }}>{ajMsg}</span>
          )}
          <button
            onClick={startMergedDownload}
            disabled={dlStatus === 'running'}
            style={{
              width: '100%', padding: '6px 10px',
              background: dlStatus === 'running' ? t.inputBg : t.accent,
              color: '#fff', border: 'none', borderRadius: 4,
              cursor: dlStatus === 'running' ? 'not-allowed' : 'pointer',
              fontWeight: 600, fontSize: 12,
            }}
          >
            {dlStatus === 'running'
              ? tr('stackSummary.downloading')
              : tr('stackSummary.downloadSlcOrbit', { count: checkedCount })}
          </button>
          {dlStatus !== 'idle' && (
            <span style={{ fontSize: 10, color: dlColor, wordBreak: 'break-all' }}>{dlMsg}</span>
          )}
        </div>
      )}

      {/* Stack list */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {stacks.map(s => {
          const dir      = (s.flightDirection ?? '').toUpperCase()
          const dirColor = dir === 'ASCENDING' ? '#f39c12' : dir === 'DESCENDING' ? '#00bcd4' : t.textMuted
          const isActive      = s.stackKey === selectedStackKey
          const isChecked     = checked.has(s.stackKey)
          const isTriggered   = triggered.has(s.stackKey)
          const triggerColor  = dlStatus === 'done' ? '#4caf50' : dlStatus === 'error' ? '#e53935' : t.accent
          const triggerLabel  = dlStatus === 'running' ? '⬇' : dlStatus === 'done' ? '✓' : dlStatus === 'error' ? '✗' : '⬇'
          return (
            <div
              key={s.stackKey}
              onClick={() => onStackClick(s.representativeFeature)}
              onMouseEnter={() => onStackHover(s.stackKey)}
              onMouseLeave={() => onStackHover(null)}
              style={{
                padding: '10px 12px',
                borderBottom: `1px solid ${t.border}`,
                cursor: 'pointer',
                background: isTriggered
                  ? `${triggerColor}33`
                  : isChecked
                    ? `${t.accent}33`
                    : isActive ? t.inputBg : 'transparent',
                borderLeft: isTriggered
                  ? `3px solid ${triggerColor}`
                  : isChecked
                    ? `3px solid ${t.accent}`
                    : '3px solid transparent',
                boxSizing: 'border-box',
                display: 'flex', alignItems: 'flex-start', gap: 8,
              }}
            >
              <input
                type="checkbox"
                checked={isChecked}
                onClick={e => toggleCheck(s.stackKey, e)}
                onChange={() => {}}
                style={{ accentColor: t.accent, cursor: 'pointer', marginTop: 2, flexShrink: 0 }}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 5 }}>
                  <span style={{ color: t.text, fontWeight: 600, fontSize: 12 }}>
                    {tr('scenePanel.pathFrame', { path: s.path, frame: s.frame })}
                  </span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    {isTriggered && (
                      <span style={{
                        color: triggerColor, fontSize: 10, fontWeight: 700,
                        background: `${triggerColor}22`, borderRadius: 3, padding: '1px 5px',
                      }}>
                        {triggerLabel}
                      </span>
                    )}
                    <span style={{
                      color: dirColor, fontSize: 10, fontWeight: 700,
                      background: `${dirColor}22`, borderRadius: 3, padding: '1px 6px',
                    }}>
                      {dir === 'ASCENDING' ? 'ASC' : dir === 'DESCENDING' ? 'DESC' : dir || '—'}
                    </span>
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{ color: t.textMuted, fontSize: 11 }}>
                    {s.startDate} – {s.endDate}
                  </span>
                  <span style={{
                    color: t.accent, fontSize: 11, fontWeight: 600,
                    background: `${t.accent}22`, borderRadius: 3, padding: '1px 6px',
                  }}>
                    {s.sceneCount}
                  </span>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
