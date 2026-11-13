import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/lineage'
const VERSION_ENDPOINT = '/api/field-versions'
const columns = [
  "节点编号",
  "节点名称",
  "节点类型",
  "字段映射",
  "引用版本编号",
  "字段业务含义",
  "版本更新时间",
  "转换逻辑",
  "图谱版本"
]
const statuses = [
  "草稿",
  "待审批",
  "已生效",
  "已废弃"
]
const stats = [
  {
    "label": "血缘节点总数",
    "value": 0
  },
  {
    "label": "已生效节点",
    "value": 0
  },
  {
    "label": "待审批节点",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

export default function LineageView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [versions, setVersions] = useState<Row[]>([])
  const [bindRow, setBindRow] = useState<Row | null>(null)
  const [bindVersionId, setBindVersionId] = useState('')
  const filterFields = useMemo(() => ["节点编号", "节点名称", "节点类型"], [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('血缘节点列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '血缘图谱列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function openBind(row: Row) {
    setBindRow(row)
    setBindVersionId('')
    setErrorMessage('')
    const response = await request(`${VERSION_ENDPOINT}?size=200`)
    if (response.ok) {
      const payload = await response.json()
      setVersions((payload.items ?? []).filter((v: Row) => v.status === '待审批' || v.status === '已生效'))
    }
  }

  async function confirmBind() {
    if (!bindRow || !bindVersionId) {
      setErrorMessage('请选择要绑定的字段含义版本')
      return
    }
    try {
      const response = await request(`${ENDPOINT}/${bindRow.id}/bind-version`, {
        method: 'POST',
        body: JSON.stringify({ values: { field_version_id: Number(bindVersionId) } }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        setErrorMessage(payload.message || '版本绑定失败')
        return
      }
      setNotice(payload.message)
      setBindRow(null)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '版本绑定失败')
    }
  }

  return (
    <section className="page" data-module="lineage">
      <header className="page-head">
        <div>
          <h2>血缘图谱管理</h2>
          <p className="page-desc">
            字段映射通过版本引用字段含义：血缘节点与元数据记录页采用同一字段说明版本，
            含义、版本号与更新时间完全一致，历史引用不会因新版本发布而改变。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出血缘图谱清单
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
                <td key={column} className={column === '字段业务含义' || column === '字段映射' ? 'cell-wrap' : ''}>
                  {column === '引用版本编号' && row[column] ? (
                    <span className="badge badge-已生效">{String(row[column])}</span>
                  ) : (
                    row[column] ?? '—'
                  )}
                </td>
              ))}
              <td className="row-actions">
                <button className="link" type="button" onClick={() => void openBind(row)}>
                  {row.引用版本编号 ? '更换版本引用' : '绑定字段版本'}
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无血缘图谱数据</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条血缘图谱记录</span>
        {notice && <span className="notice-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      {bindRow && (
        <div className="modal-mask" onClick={() => setBindRow(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>为 {String(bindRow.节点编号)} 绑定字段含义版本</h3>
            <p className="page-desc">
              仅可绑定待审批或已生效版本；绑定后字段映射与字段业务含义按该版本快照展示。
            </p>
            <label className="modal-field">
              <span>选择字段版本</span>
              <select value={bindVersionId} onChange={(e) => setBindVersionId(e.target.value)}>
                <option value="">请选择版本</option>
                {versions.map((v) => (
                  <option key={String(v.id)} value={String(v.id)}>
                    {String(v.版本编号)} ｜ {String(v.记录编号)} ｜ {String(v.字段名称)} ｜ {String(v.status)}
                  </option>
                ))}
              </select>
            </label>
            <div className="modal-actions">
              <button className="btn" type="button" onClick={() => setBindRow(null)}>取消</button>
              <button className="btn primary" type="button" onClick={() => void confirmBind()}>确认绑定</button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
