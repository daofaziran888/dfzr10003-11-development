import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/catalog'
const columns = [
  "记录编号",
  "表名称",
  "变更类型",
  "变更字段",
  "变更前值",
  "变更后值",
  "变更批次",
  "变更时间",
  "引用版本编号",
  "字段业务含义",
  "版本更新时间"
]
const actions = [
  "标记复核",
  "归档记录"
]
const statuses = [
  "已记录",
  "已复核",
  "已归档"
]
const stats = [
  {
    "label": "演进记录总数",
    "value": 0
  },
  {
    "label": "已复核记录",
    "value": 0
  },
  {
    "label": "已归档记录",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

export default function CatalogView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => ["记录编号", "表名称", "变更类型"], [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}&size=200`)
      if (!response.ok) {
        throw new Error('演进记录列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '表结构演进列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message || '表结构演进动作未生效')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '表结构演进操作失败')
    }
  }

  return (
    <section className="page" data-module="catalog">
      <header className="page-head">
        <div>
          <h2>表结构演进管理</h2>
          <p className="page-desc">
            每次字段含义版本发布都会生成一条演进记录并保留版本引用；历史批次回看时展示的仍是当时采用的版本快照，
            不会只剩最后一份变更。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出表结构演进清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item, index) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">
              {index === 0
                ? total
                : rows.filter((r) => r.status === (index === 1 ? '已复核' : '已归档')).length}
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
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => (
                <td key={column} className={column === '字段业务含义' || column === '变更前值' || column === '变更后值' ? 'cell-wrap' : ''}>
                  {column === '引用版本编号' && row[column] ? (
                    <span className="badge badge-已生效">{String(row[column])}</span>
                  ) : column === '变更类型' && String(row[column]).includes('回填') ? (
                    <span className="tag tag-backfill">{String(row[column])}</span>
                  ) : (
                    row[column] ?? '—'
                  )}
                </td>
              ))}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无表结构演进数据</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条表结构演进记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
