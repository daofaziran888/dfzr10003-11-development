import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/field-versions'

type Row = Record<string, string | number | boolean | null>

const statuses = ['草稿', '待审批', '已生效', '已驳回']

function today() {
  return new Date().toISOString().slice(0, 10)
}

type ModalState =
  | { kind: 'none' }
  | { kind: 'draft'; row: Row | null; metadataId: number; values: Record<string, string> }
  | { kind: 'publish'; row: Row; effectiveDate: string; batch: string }
  | { kind: 'backfill'; row: Row; effectiveDate: string; batch: string }

export default function FieldVersionView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [modal, setModal] = useState<ModalState>({ kind: 'none' })
  // 版本解析工具
  const [resolveMetaId, setResolveMetaId] = useState('2')
  const [resolveDate, setResolveDate] = useState(today())
  const [resolveResult, setResolveResult] = useState<Row | null>(null)
  const [timeline, setTimeline] = useState<Row[]>([])
  const [conflicts, setConflicts] = useState<Row[]>([])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(
      Object.entries(filters).filter(([, value]) => value !== ''),
    ).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}&size=200`)
      if (!response.ok) throw new Error('字段版本列表读取失败')
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '字段版本列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  const stats = useMemo(() => ([
    { label: '字段版本总数', value: total },
    { label: '草稿', value: rows.filter((r) => r.status === '草稿').length },
    { label: '待审批', value: rows.filter((r) => r.status === '待审批').length },
    { label: '已生效', value: rows.filter((r) => r.status === '已生效').length },
  ]), [rows, total])

  async function callAction(path: string, body?: Record<string, unknown>) {
    setErrorMessage('')
    setNotice('')
    const response = await request(path, {
      method: 'POST',
      body: JSON.stringify(body ?? { values: {} }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      setErrorMessage(payload.message || '操作未生效，请稍后重试')
      return false
    }
    setNotice(payload.message)
    return true
  }

  function openCreateDraft() {
    setModal({
      kind: 'draft',
      row: null,
      metadataId: 0,
      values: { 业务含义: '', 统计信息: '', 负责人: '', 变更说明: '' },
    })
  }

  function openEditDraft(row: Row) {
    setModal({
      kind: 'draft',
      row,
      metadataId: Number(row.metadata_id),
      values: {
        业务含义: String(row.业务含义 ?? ''),
        统计信息: String(row.统计信息 ?? ''),
        负责人: String(row.负责人 ?? ''),
        变更说明: String(row.变更说明 ?? ''),
      },
    })
  }

  async function submitDraft() {
    if (modal.kind !== 'draft') return
    const { row, metadataId, values } = modal
    let ok = false
    if (row === null) {
      if (!metadataId) {
        setErrorMessage('请填写元数据记录 id（metadata_id）')
        return
      }
      ok = await callAction(`${ENDPOINT}/drafts`, {
        values: { metadata_id: metadataId, ...values },
        remark: values.变更说明,
      })
    } else {
      ok = await callAction(`${ENDPOINT}/${row.id}/draft`, { values })
    }
    if (ok) {
      setModal({ kind: 'none' })
      await reload()
    }
  }

  async function submitApproval(row: Row) {
    if (await callAction(`${ENDPOINT}/${row.id}/submit`)) await reload()
  }

  async function confirmPublish() {
    if (modal.kind !== 'publish') return
    const ok = await callAction(`${ENDPOINT}/${modal.row.id}/publish`, {
      values: { effective_date: modal.effectiveDate, batch: modal.batch },
    })
    if (ok) {
      setModal({ kind: 'none' })
      await reload()
    }
  }

  async function confirmBackfill() {
    if (modal.kind !== 'backfill') return
    if (!modal.effectiveDate) {
      setErrorMessage('回填发布必须指定历史生效日期')
      return
    }
    const ok = await callAction(`${ENDPOINT}/${modal.row.id}/backfill`, {
      values: { effective_date: modal.effectiveDate, batch: modal.batch },
    })
    if (ok) {
      setModal({ kind: 'none' })
      await reload()
    }
  }

  async function reject(row: Row) {
    if (await callAction(`${ENDPOINT}/${row.id}/reject`, { values: {} }, )) {
      await reload()
    }
  }

  async function runResolve() {
    setErrorMessage('')
    try {
      const response = await request(
        `${ENDPOINT}/records/${resolveMetaId}/resolve?target_date=${resolveDate}`,
      )
      if (!response.ok) throw new Error('版本解析失败')
      setResolveResult(await response.json())
      const [tl, cf] = await Promise.all([
        request(`${ENDPOINT}/records/${resolveMetaId}/timeline`),
        request(`${ENDPOINT}/records/${resolveMetaId}/conflicts?target_date=${resolveDate}`),
      ])
      setTimeline((await tl.json()).items ?? [])
      setConflicts((await cf.json()).items ?? [])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '版本解析失败')
    }
  }

  return (
    <section className="page" data-module="field-version">
      <header className="page-head">
        <div>
          <h2>字段含义版本发布</h2>
          <p className="page-desc">
            每次修改业务含义、统计信息或负责人都会形成新草稿，按草稿 → 待审批 → 已生效推进；
            已生效版本不可覆盖，晚到修正以回填方式进入历史生效区间，血缘与演进记录统一引用实际采用的版本。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={openCreateDraft}>
            新建含义草稿
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form
        className="filter-bar"
        onSubmit={(event) => {
          event.preventDefault()
          void reload()
        }}
      >
        <label className="filter-item">
          <span>记录编号</span>
          <input
            placeholder="如 META-0002"
            value={filters.record_code ?? ''}
            onChange={(e) => setFilters((p) => ({ ...p, record_code: e.target.value }))}
          />
        </label>
        <label className="filter-item">
          <span>状态</span>
          <select
            value={filters.status ?? ''}
            onChange={(e) => setFilters((p) => ({ ...p, status: e.target.value }))}
          >
            <option value="">全部</option>
            {statuses.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label className="filter-item">
          <span>关键字</span>
          <input
            placeholder="版本编号 / 字段名称"
            value={filters.keyword ?? ''}
            onChange={(e) => setFilters((p) => ({ ...p, keyword: e.target.value }))}
          />
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>
          重置条件
        </button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {['版本编号', '记录编号', '字段名称', '业务含义', '负责人', '状态', '发布批次', '生效区间', '回填', '完整度', '更新时间', '操作'].map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              <td>{row.版本编号 ?? '—'}</td>
              <td>{row.记录编号 ?? '—'}</td>
              <td>{row.字段名称 ?? '—'}</td>
              <td className="cell-wrap" title={String(row.业务含义 ?? '')}>{row.业务含义 ?? '—'}</td>
              <td>{row.负责人 ?? '—'}</td>
              <td><span className={`badge badge-${String(row.status)}`}>{String(row.status)}</span></td>
              <td>{row.发布批次 || '—'}</td>
              <td>
                {row.生效时间 ? `${row.生效时间} ~ ${row.失效时间 || '至今'}` : '—'}
              </td>
              <td>{row.回填标记 ? <span className="tag tag-backfill">回填</span> : '—'}</td>
              <td>{row.完整度 ?? '—'}</td>
              <td>{row.更新时间 ?? '—'}</td>
              <td className="row-actions">
                {row.status === '草稿' && (
                  <>
                    <button className="link" type="button" onClick={() => openEditDraft(row)}>编辑草稿</button>
                    <button className="link" type="button" onClick={() => void submitApproval(row)}>提交审批</button>
                  </>
                )}
                {row.status === '待审批' && (
                  <>
                    <button
                      className="link"
                      type="button"
                      onClick={() => setModal({ kind: 'publish', row, effectiveDate: today(), batch: '' })}
                    >
                      发布生效
                    </button>
                    <button
                      className="link"
                      type="button"
                      onClick={() => setModal({ kind: 'backfill', row, effectiveDate: '', batch: '' })}
                    >
                      回填发布
                    </button>
                    <button className="link link-danger" type="button" onClick={() => void reject(row)}>驳回</button>
                  </>
                )}
                {row.status === '已生效' && <span className="muted-text">不可覆盖</span>}
                {row.status === '已驳回' && <span className="muted-text">已终止</span>}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={12}>暂无字段版本，可先新建含义草稿</td>
            </tr>
          )}
        </tbody>
      </table>

      <div className="version-tool">
        <h3>生效区间解析与冲突排查</h3>
        <p className="page-desc">
          两个说明同时声称适用于同一字段时间段时，按「生效时间 → 发布批次 → 版本完整度」定优先级，
          以能够覆盖目标日期且已生效的版本为准。
        </p>
        <div className="filter-bar">
          <label className="filter-item">
            <span>元数据记录 id</span>
            <input value={resolveMetaId} onChange={(e) => setResolveMetaId(e.target.value)} />
          </label>
          <label className="filter-item">
            <span>目标日期</span>
            <input type="date" value={resolveDate} onChange={(e) => setResolveDate(e.target.value)} />
          </label>
          <button className="btn primary" type="button" onClick={() => void runResolve()}>解析实际采用版本</button>
        </div>
        {resolveResult && (
          <div className="resolve-panel">
            {resolveResult.命中 ? (
              <div className="resolve-hit">
                <span className="badge badge-已生效">{String(resolveResult.版本编号)}</span>
                <span>业务含义：{String(resolveResult.业务含义)}</span>
                <span className="muted-text">判定依据：{String(resolveResult.优先级说明 ?? '')}</span>
                <span className="muted-text">更新时间：{String(resolveResult.更新时间 ?? '')}</span>
              </div>
            ) : (
              <div className="error-text">{String(resolveResult.message ?? '目标日期没有命中版本')}</div>
            )}
            {timeline.length > 0 && (
              <div className="timeline">
                <strong>生效区间时间线（历史批次可回看当时说明）</strong>
                {timeline.map((t) => (
                  <div key={String(t.版本编号)} className="timeline-item">
                    <span className="badge badge-已生效">{String(t.版本编号)}</span>
                    <span>{String(t.生效时间)} ~ {String(t.失效时间)}</span>
                    <span>批次 {String(t.发布批次)}</span>
                    {t.回填标记 ? <span className="tag tag-backfill">晚到回填</span> : null}
                    <span className="cell-wrap">{String(t.业务含义)}</span>
                  </div>
                ))}
              </div>
            )}
            {conflicts.length > 0 && (
              <div className="conflict-panel">
                <strong>时间段冲突（{conflicts.length} 组）</strong>
                {conflicts.map((c, i) => (
                  <div key={i} className="conflict-item">
                    <span>胜出：{String(c.胜出版本)} ｜ 让位：{String(c.让位版本)}</span>
                    <span className="muted-text">{String(c.判定依据)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <footer className="page-foot">
        <span>共 {total} 条字段版本</span>
        {notice && <span className="notice-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      {modal.kind === 'draft' && (
        <div className="modal-mask" onClick={() => setModal({ kind: 'none' })}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>{modal.row === null ? '新建字段含义草稿' : `编辑草稿 ${String(modal.row.版本编号)}`}</h3>
            {modal.row === null && (
              <label className="modal-field">
                <span>元数据记录 id（metadata_id）</span>
                <input
                  type="number"
                  value={modal.metadataId || ''}
                  onChange={(e) => setModal({ ...modal, metadataId: Number(e.target.value) })}
                />
              </label>
            )}
            {(['业务含义', '统计信息', '负责人', '变更说明'] as const).map((field) => (
              <label key={field} className="modal-field">
                <span>{field}{field === '业务含义' ? ' *' : ''}</span>
                {field === '业务含义' ? (
                  <textarea
                    rows={3}
                    value={modal.values[field]}
                    onChange={(e) => setModal({ ...modal, values: { ...modal.values, [field]: e.target.value } })}
                  />
                ) : (
                  <input
                    value={modal.values[field]}
                    onChange={(e) => setModal({ ...modal, values: { ...modal.values, [field]: e.target.value } })}
                  />
                )}
              </label>
            ))}
            <div className="modal-actions">
              <button className="btn" type="button" onClick={() => setModal({ kind: 'none' })}>取消</button>
              <button className="btn primary" type="button" onClick={() => void submitDraft()}>保存草稿</button>
            </div>
          </div>
        </div>
      )}

      {modal.kind === 'publish' && (
        <div className="modal-mask" onClick={() => setModal({ kind: 'none' })}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>发布版本 {String(modal.row.版本编号)}</h3>
            <p className="page-desc">发布后已生效版本不可覆盖，元数据记录、血缘节点与新演进记录将统一引用该版本。</p>
            <label className="modal-field">
              <span>生效时间</span>
              <input type="date" value={modal.effectiveDate} onChange={(e) => setModal({ ...modal, effectiveDate: e.target.value })} />
            </label>
            <label className="modal-field">
              <span>发布批次（留空自动生成）</span>
              <input value={modal.batch} onChange={(e) => setModal({ ...modal, batch: e.target.value })} />
            </label>
            <div className="modal-actions">
              <button className="btn" type="button" onClick={() => setModal({ kind: 'none' })}>取消</button>
              <button className="btn primary" type="button" onClick={() => void confirmPublish()}>确认发布</button>
            </div>
          </div>
        </div>
      )}

      {modal.kind === 'backfill' && (
        <div className="modal-mask" onClick={() => setModal({ kind: 'none' })}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>晚到修正回填发布 {String(modal.row.版本编号)}</h3>
            <p className="page-desc">
              修正版将插入指定日期起的历史生效区间，旧已生效版本保留不覆盖，历史批次仍可回看当时说明。
            </p>
            <label className="modal-field">
              <span>修正生效日期 *</span>
              <input type="date" value={modal.effectiveDate} onChange={(e) => setModal({ ...modal, effectiveDate: e.target.value })} />
            </label>
            <label className="modal-field">
              <span>回填批次（留空自动生成 BF 批次）</span>
              <input value={modal.batch} onChange={(e) => setModal({ ...modal, batch: e.target.value })} />
            </label>
            <div className="modal-actions">
              <button className="btn" type="button" onClick={() => setModal({ kind: 'none' })}>取消</button>
              <button className="btn primary" type="button" onClick={() => void confirmBackfill()}>确认回填</button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
