import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/metadata'
const VERSION_ENDPOINT = '/api/field-versions'
const columns = [
  "记录编号",
  "表名称",
  "字段名称",
  "字段类型",
  "业务含义",
  "统计信息",
  "负责人",
  "当前版本编号",
  "开放版本状态",
  "版本更新时间"
]
const statuses = [
  "草稿",
  "待审批",
  "已生效",
  "已废弃"
]
const stats = [
  {
    "label": "元数据记录总数",
    "value": 0
  },
  {
    "label": "已生效记录",
    "value": 0
  },
  {
    "label": "待审批记录",
    "value": 0
  }
]

type Row = Record<string, string | number | boolean | null>

export default function MetadataView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [draftRow, setDraftRow] = useState<Row | null>(null)
  const [draftForm, setDraftForm] = useState({ 业务含义: '', 统计信息: '', 负责人: '', 变更说明: '' })
  const filterFields = useMemo(() => ["记录编号", "表名称", "字段名称"], [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('元数据记录列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '元数据目录列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  function openDraft(row: Row) {
    setErrorMessage('')
    setDraftRow(row)
    setDraftForm({
      业务含义: String(row.业务含义 ?? ''),
      统计信息: String(row.统计信息 ?? ''),
      负责人: String(row.负责人 ?? ''),
      变更说明: '',
    })
  }

  async function saveDraft() {
    if (!draftRow) return
    if (!draftForm.业务含义.trim()) {
      setErrorMessage('业务含义为必填项')
      return
    }
    try {
      const response = await request(`${VERSION_ENDPOINT}/drafts`, {
        method: 'POST',
        body: JSON.stringify({
          values: { metadata_id: draftRow.id, ...draftForm },
          remark: draftForm.变更说明,
        }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        setErrorMessage(payload.message || '草稿创建失败')
        return
      }
      setNotice(payload.message)
      setDraftRow(null)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '草稿创建失败')
    }
  }

  return (
    <section className="page" data-module="metadata">
      <header className="page-head">
        <div>
          <h2>元数据目录管理</h2>
          <p className="page-desc">
            字段业务含义、统计信息与负责人通过版本发布管理：修改即生成新草稿，已生效版本不可覆盖；
            页面展示的含义、版本号与更新时间与血缘页、演进记录保持一致。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出元数据目录清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item, index) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">
              {index === 0 ? total : rows.filter((r) => r.status === (index === 1 ? '已生效' : '待审批')).length}
            </strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>版本操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => (
                <td key={column} className={column === '业务含义' ? 'cell-wrap' : ''}>
                  {column === '开放版本状态' && row[column] ? (
                    <span className={`badge badge-${String(row[column])}`}>{String(row[column])}{row.开放版本编号 ? `（${String(row.开放版本编号)}）` : ''}</span>
                  ) : (
                    row[column] ?? '—'
                  )}
                </td>
              ))}
              <td className="row-actions">
                <button className="link" type="button" onClick={() => openDraft(row)}>
                  修改含义生成草稿
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无元数据目录数据</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条元数据目录记录</span>
        {notice && <span className="notice-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      {draftRow && (
        <div className="modal-mask" onClick={() => setDraftRow(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>为 {String(draftRow.记录编号)} / {String(draftRow.字段名称)} 新建含义草稿</h3>
            <p className="page-desc">
              修改业务含义、统计信息或负责人后形成新草稿，不会覆盖任何已生效版本；
              该字段存在待审批版本时将被拒绝。
            </p>
            {(['业务含义', '统计信息', '负责人', '变更说明'] as const).map((field) => (
              <label key={field} className="modal-field">
                <span>{field}{field === '业务含义' ? ' *' : ''}</span>
                {field === '业务含义' ? (
                  <textarea
                    rows={3}
                    value={draftForm[field]}
                    onChange={(e) => setDraftForm({ ...draftForm, [field]: e.target.value })}
                  />
                ) : (
                  <input
                    value={draftForm[field]}
                    onChange={(e) => setDraftForm({ ...draftForm, [field]: e.target.value })}
                  />
                )}
              </label>
            ))}
            <div className="modal-actions">
              <button className="btn" type="button" onClick={() => setDraftRow(null)}>取消</button>
              <button className="btn primary" type="button" onClick={() => void saveDraft()}>生成草稿</button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
