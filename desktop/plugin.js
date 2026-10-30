/**
 * last_in 舰队面板 — 桌面 UI（状态条 chip + 布局面板）。
 *
 * 数据：ctx.rest('/snapshot') → 本插件 Python 后端（~/.hermes/plugins/last-in/dashboard/plugin_api.py）。
 * 口径：last_in = 各 profile canonical Bot Chat 末笔 API call 的 in=（含缓存命中 ≈ 上下文实长）；
 *       ≥ threshold 警示色，与轮换 cron 同口径。默认 5s 轮询。
 * 规矩：纯 ESM、jsx() 调用（无 JSX 语法）、只 import plugin-sdk/react、颜色只用 --ui-* 变量。
 */

import { cn, haptic, host, Tip } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'last-in'

function fmtK(n) {
  if (!n) return '0'
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
  if (n >= 1000) return Math.round(n / 1000) + 'k'
  return String(n)
}

function relTime(epoch) {
  if (!epoch) return '—'
  const dt = Math.max(0, Date.now() / 1000 - epoch)
  if (dt < 90) return Math.round(dt) + 's'
  if (dt < 5400) return Math.round(dt / 60) + 'm'
  if (dt < 172800) return Math.round(dt / 3600) + 'h'
  return Math.round(dt / 86400) + 'd'
}

/** 单行：席名 last_in/阈值 忙点。超阈警示色；近阈（≥85%）提示色。 */
function BotRow({ b }) {
  const th = b.threshold || 0
  const pct = th ? (b.last_in * 100) / th : 0
  const over = th > 0 && b.last_in >= th
  const near = !over && pct >= 85
  const dot = jsx('span', {
    className: 'shrink-0 w-1.5 h-1.5 rounded-full ' +
      (b.busy
        ? 'bg-(--ui-green)'
        : 'bg-transparent border border-(--ui-stroke-tertiary)'),
    title: b.busy ? '回合进行中' : '空闲'
  })
  const name = jsx('span', {
    className: 'truncate font-medium',
    title: (b.model || '?') + (b.window ? ' · 窗口 ' + fmtK(b.window) : ''),
    children: b.profile
  })
  const left = jsxs('span', {
    className: 'flex items-center gap-1.5 min-w-0',
    children: [dot, name]
  })
  const right = jsxs('span', {
    className: 'shrink-0 tabular-nums',
    title: '阈值 ' + fmtK(th) + (b.window ? '（窗口 ' + fmtK(b.window) + ' × 80%）' : ''),
    children: [
      fmtK(b.last_in),
      jsx('span', { className: 'text-(--ui-text-quaternary) ml-1', children: Math.round(pct) + '%' }),
      jsx('span', { className: 'text-(--ui-text-quaternary) ml-1', children: relTime(b.last_call_at) })
    ]
  })
  return jsxs('div', {
    className: 'flex items-center justify-between gap-2 px-1.5 py-[3px] rounded text-xs ' +
      (over ? 'text-(--ui-red)' : near ? 'text-(--ui-text-secondary)' : 'text-(--ui-text-tertiary)'),
    children: [left, right]
  })
}

function FleetPane() {
  const [snap, setSnap] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    let alive = true
    let timer = null
    const tick = async () => {
      try {
        const d = await ctxRest('/snapshot')
        if (!alive) return
        setSnap(d)
        setErr(null)
      } catch (e) {
        if (!alive) return
        setErr(String(e && e.message ? e.message : e))
      } finally {
        if (alive) timer = setTimeout(tick, 5000)
      }
    }
    tick()
    return () => { alive = false; if (timer) clearTimeout(timer) }
  }, [])

  if (err) {
    return jsx('div', {
      className: 'p-3 text-xs text-(--ui-red)',
      children: '后端不可达：' + err + '（需重启桌面后端加载插件 API）'
    })
  }
  if (!snap) {
    return jsx('div', { className: 'p-3 text-xs text-(--ui-text-tertiary)', children: '加载中…' })
  }
  const ratio = Math.round((snap.threshold_ratio || 0.8) * 100)
  return jsxs('div', {
    className: 'flex h-full flex-col gap-1.5 p-2 text-sm overflow-y-auto',
    children: [
      jsxs('div', {
        className: 'flex items-baseline justify-between px-1.5',
        children: [
          jsx('span', { className: 'text-xs font-medium text-(--ui-text-tertiary)', children: 'last_in（上下文实长）' }),
          jsxs('span', {
            className: 'text-xs tabular-nums text-(--ui-text-quaternary)',
            children: [
              '阈值=窗口×' + ratio + '% · ' + snap.over_threshold + '/' + snap.count + ' 超阈',
              snap.scan_ms != null ? ' · ' + snap.scan_ms + 'ms' : ''
            ]
          })
        ]
      }),
      jsx('div', {
        className: 'flex flex-col',
        children: snap.bots.map(b => jsx(BotRow, { b }, b.profile))
      }),
      jsx('div', {
        className: 'px-1.5 pt-1 text-[10px] text-(--ui-text-quaternary)',
        children: '口径同轮换 cron（含缓存命中）；排除：' + (snap.excluded.join(', ') || '无')
      })
    ]
  })
}

/** 状态条 chip：超阈席数（红）或总量概览（灰）。点击开/关面板聚焦。 */
function FleetChip() {
  const [over, setOver] = useState(null)

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const d = await ctxRest('/snapshot')
        if (alive) setOver(d.over_threshold)
      } catch {
        if (alive) setOver(null)
      }
    }
    tick()
    const timer = setInterval(tick, 15000)
    return () => { alive = false; clearInterval(timer) }
  }, [])

  return jsx(Tip, {
    label: 'last_in 舰队面板 — 各席末回合输入 token（≥250k 将被轮换 cron 处理）',
    children: jsx('button', {
      className: cn(
        'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] transition-colors',
        over == null
          ? 'text-(--ui-text-tertiary)'
          : over > 0 ? 'text-(--ui-red)' : 'text-(--ui-green)'
      ),
      type: 'button',
      onClick: () => {
        haptic('tap')
        host.navigate('/')
      },
      children: over == null ? 'fleet…' : 'fleet ' + over
    })
  })
}

/** ctx.rest 由 loader 注入 register(ctx)；这里存到模块级供组件使用。 */
let ctxRest = null

export default {
  id: ID,
  name: 'last_in 舰队面板',
  register(ctx) {
    ctxRest = (path, opts) => ctx.rest(path, opts)

    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'last_in',
      data: { placement: 'right', width: '237px' },
      render: () => jsx(FleetPane, {})
    })

    ctx.register({
      id: 'chip',
      area: 'statusBar.right',
      order: 130,
      render: () => jsx(FleetChip, {})
    })
  }
}
