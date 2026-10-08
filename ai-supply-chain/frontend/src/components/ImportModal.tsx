import { InboxOutlined, WarningOutlined } from '@ant-design/icons'
import {
  Alert,
  Button,
  Descriptions,
  Divider,
  InputNumber,
  Modal,
  Radio,
  Select,
  Space,
  Steps,
  Table,
  Tag,
  Typography,
  Upload,
} from 'antd'
import { useState } from 'react'
import { apiPostForm } from '../api/client'
import type {
  CustomerSourceType,
  ImportCommitResponse,
  ImportPreviewResponse,
} from '../api/types'
import { SKIP_REASON_LABELS } from '../api/types'
import EvidenceNumber from './EvidenceNumber'

const { Text, Paragraph } = Typography

/** 下拉里"忽略此列"的哨兵值（提交时转成 target=null） */
const IGNORE = '__ignore__'

type Props = {
  open: boolean
  onClose: () => void
  /** 导入成功后通知列表刷新 */
  onImported: (batchId: number) => void
}

/**
 * 导入弹窗：上传 → 确认映射 → 结果（三步），对应后端 preview / commit 两步接口。
 *
 * ★ 映射必须由用户确认后才提交（防止"把备注当电话"）。
 * ★ PARTIAL 用 warning 色并且标题写清「部分导入：N 成功 / M 跳过」，绝不让人以为全成了。
 */
export default function ImportModal({ open, onClose, onImported }: Props) {
  const [file, setFile] = useState<File | null>(null)
  const [fileList, setFileList] = useState<any[]>([])
  const [headerRow, setHeaderRow] = useState(1)
  const [sourceType, setSourceType] = useState<CustomerSourceType>('TEST')
  const [preview, setPreview] = useState<ImportPreviewResponse | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [result, setResult] = useState<ImportCommitResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const reset = () => {
    setFile(null)
    setFileList([])
    setHeaderRow(1)
    setSourceType('TEST')
    setPreview(null)
    setMapping({})
    setResult(null)
    setError(null)
    setBusy(false)
  }

  const close = () => {
    reset()
    onClose()
  }

  const pickFile = (picked: File) => {
    setFile(picked)
    setFileList([{ uid: picked.name, name: picked.name, status: 'done' } as any])
    setPreview(null)
    setResult(null)
    setError(null)
  }

  /** 第一步：只解析、不入库 */
  const runPreview = async () => {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('header_row', String(headerRow))
      const data = await apiPostForm<ImportPreviewResponse>('/api/imports/preview', form)
      setPreview(data)
      setMapping(
        Object.fromEntries(
          data.columns.map((c) => [c.column, c.target ?? IGNORE]),
        ),
      )
    } catch (err) {
      setError((err as { message?: string }).message ?? '预览失败')
    } finally {
      setBusy(false)
    }
  }

  /** 第二步：带用户确认后的映射表 + sha256 真正入库 */
  const runCommit = async () => {
    if (!file || !preview) return
    setBusy(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('sha256', preview.file_sha256)
      form.append('source_type', sourceType)
      form.append('header_row', String(headerRow))
      if (preview.encoding) form.append('encoding', preview.encoding)
      form.append(
        'mapping',
        JSON.stringify(
          preview.headers.map((column) => ({
            column,
            target: mapping[column] === IGNORE ? null : mapping[column],
          })),
        ),
      )
      const data = await apiPostForm<ImportCommitResponse>('/api/imports/commit', form)
      setResult(data)
      onImported(data.batch_id)
    } catch (err) {
      setError((err as { message?: string }).message ?? '导入失败')
    } finally {
      setBusy(false)
    }
  }

  const step = result ? 2 : preview ? 1 : 0
  const targetOptions = [
    { value: IGNORE, label: '忽略此列' },
    ...(preview?.target_fields ?? []).map((f) => ({ value: f.key, label: `${f.label}（${f.key}）` })),
  ]

  return (
    <Modal
      open={open}
      title="导入 Excel / CSV"
      width={880}
      onCancel={close}
      mask={{ closable: false }}
      footer={
        <Space>
          <Button onClick={close}>关闭</Button>
          {step === 0 && (
            <Button type="primary" loading={busy} disabled={!file} onClick={runPreview}>
              解析预览
            </Button>
          )}
          {step === 1 && (
            <Button type="primary" loading={busy} onClick={runCommit}>
              确认映射并导入
            </Button>
          )}
          {step === 2 && (
            <Button
              type="primary"
              onClick={() => {
                reset()
              }}
            >
              继续导入下一个文件
            </Button>
          )}
        </Space>
      }
    >
      <Steps
        size="small"
        current={step}
        style={{ marginBottom: 20 }}
        items={[{ title: '选择文件' }, { title: '确认映射' }, { title: '导入结果' }]}
      />

      {error && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message="导入未完成"
          description={error}
        />
      )}

      {/* ── 第一步：选文件 ── */}
      {step === 0 && (
        <>
          <Upload.Dragger
            accept=".csv,.xlsx,.xlsm"
            maxCount={1}
            fileList={fileList as any}
            beforeUpload={(picked) => {
              pickFile(picked as unknown as File)
              return false // 不自动上传：先预览，用户确认后才入库
            }}
            onRemove={() => {
              setFile(null)
              setFileList([])
              setPreview(null)
              return true
            }}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽 CSV / XLSX 文件到这里</p>
            <p className="ant-upload-hint">单文件 ≤ 10MB、数据行 ≤ 50,000；中文 CSV 支持 UTF-8 与 GBK/GB18030</p>
          </Upload.Dragger>

          <Space size={24} style={{ marginTop: 20 }} align="start" wrap>
            <Space>
              <Text>表头在第</Text>
              <InputNumber
                min={1}
                value={headerRow}
                onChange={(v) => setHeaderRow(Number(v) || 1)}
                style={{ width: 72 }}
              />
              <Text>行</Text>
            </Space>
            <Space>
              <Text>数据来源</Text>
              <Radio.Group
                value={sourceType}
                onChange={(e) => setSourceType(e.target.value)}
                optionType="button"
                options={[
                  { value: 'TEST', label: 'TEST 测试数据' },
                  { value: 'REAL', label: 'REAL 真实数据' },
                ]}
              />
            </Space>
          </Space>
          <Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
            默认按 TEST 入库：测试样本绝不能污染业务数字。真实客户数据请选 REAL。
          </Paragraph>
        </>
      )}

      {/* ── 第二步：确认映射 ── */}
      {step === 1 && preview && (
        <>
          <Descriptions size="small" bordered column={3} style={{ marginBottom: 16 }}>
            <Descriptions.Item label="文件名">{preview.filename}</Descriptions.Item>
            <Descriptions.Item label="识别编码">
              {preview.encoding ?? '不适用'}
            </Descriptions.Item>
            <Descriptions.Item label="表头行号">第 {preview.header_row} 行</Descriptions.Item>
            <Descriptions.Item label="Sheet">
              {preview.sheet_used ?? '—'}
            </Descriptions.Item>
            <Descriptions.Item label="数据行数">
              <EvidenceNumber evidence={preview.rows_total} suffix=" 行" />
            </Descriptions.Item>
            <Descriptions.Item label="未映射列">
              {preview.unmapped_columns.length} 列
            </Descriptions.Item>
          </Descriptions>

          {preview.sheet_note && (
            <Alert type="info" showIcon style={{ marginBottom: 12 }} message={preview.sheet_note} />
          )}
          {preview.encoding_note && (
            <Alert type="info" showIcon style={{ marginBottom: 12 }} message={preview.encoding_note} />
          )}

          <Text strong>列映射确认（可修改 / 可取消，取消的列不会入库）</Text>
          <Table
            size="small"
            rowKey="column"
            pagination={false}
            style={{ marginTop: 8 }}
            dataSource={preview.columns}
            columns={[
              { title: '文件里的列名', dataIndex: 'column' },
              {
                title: '自动识别',
                dataIndex: 'confidence',
                width: 110,
                render: (v: string) =>
                  v === 'auto' ? <Tag color="green">auto</Tag> : <Tag color="orange">unknown</Tag>,
              },
              {
                title: '映射到',
                width: 220,
                render: (_: unknown, record) => (
                  <Select
                    size="small"
                    style={{ width: '100%' }}
                    value={mapping[record.column] ?? IGNORE}
                    options={targetOptions}
                    onChange={(value) => setMapping((prev) => ({ ...prev, [record.column]: value }))}
                  />
                ),
              },
            ]}
          />

          {preview.sample_rows.length > 0 && (
            <>
              <Divider plain style={{ margin: '16px 0 8px' }}>
                前 {preview.sample_rows.length} 行样本
              </Divider>
              <Table
                size="small"
                rowKey={(_, index) => String(index)}
                pagination={false}
                scroll={{ x: 'max-content' }}
                dataSource={preview.sample_rows}
                columns={preview.headers.map((header) => ({
                  title: header,
                  dataIndex: header,
                  ellipsis: true,
                }))}
              />
            </>
          )}
        </>
      )}

      {/* ── 第三步：结果 ── */}
      {step === 2 && result && (
        <>
          {result.status === 'PARTIAL' ? (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              message={`部分导入：${
                (result.rows_created.value ?? 0) + (result.rows_deduplicated.value ?? 0)
              } 成功 / ${result.rows_skipped.value ?? 0} 跳过`}
              description="成功的行已入库；跳过的是坏行，明细见下方「跳过明细」。"
            />
          ) : (
            <Alert
              type="success"
              showIcon
              style={{ marginBottom: 16 }}
              message={`导入完成：批次 ${result.batch_id}`}
            />
          )}

          <Space size={40} style={{ marginBottom: 8 }} wrap>
            <div>
              <Text type="secondary">新建</Text>
              <div style={{ fontSize: 22 }}>
                <EvidenceNumber evidence={result.rows_created} />
              </div>
            </div>
            <div>
              <Text type="secondary">去重命中</Text>
              <div style={{ fontSize: 22 }}>
                <EvidenceNumber evidence={result.rows_deduplicated} />
              </div>
            </div>
            <div>
              <Text type="secondary">跳过</Text>
              <div style={{ fontSize: 22 }}>
                <EvidenceNumber evidence={result.rows_skipped} />
              </div>
            </div>
            <div>
              <Text type="secondary">文件总行数</Text>
              <div style={{ fontSize: 22 }}>
                <EvidenceNumber evidence={result.rows_total} />
              </div>
            </div>
          </Space>

          {result.skipped_columns.length > 0 && (
            <Alert
              type="info"
              showIcon
              icon={<WarningOutlined />}
              style={{ marginTop: 12 }}
              message="未映射的列（内容未入库）"
              description={result.skipped_columns.map((c) => c.column).join('、')}
            />
          )}

          {result.skipped_reasons.length > 0 && (
            <>
              <Divider plain style={{ margin: '16px 0 8px' }}>跳过明细</Divider>
              <Table
                size="small"
                rowKey={(record) => `${record.row}-${record.reason}`}
                pagination={{ pageSize: 5 }}
                dataSource={result.skipped_reasons}
                columns={[
                  { title: '行号', dataIndex: 'row', width: 80 },
                  {
                    title: '原因',
                    dataIndex: 'reason',
                    width: 140,
                    render: (reason: string) => (
                      <Tag color="red">{SKIP_REASON_LABELS[reason] ?? reason}</Tag>
                    ),
                  },
                  { title: '原始值（仅作报错上下文）', dataIndex: 'raw' },
                ]}
              />
            </>
          )}

          {result.conflicts.length > 0 && (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 12 }}
              message={`${result.conflicts.length} 条客户需要人工裁决（未自动合并）`}
              description={result.conflicts
                .map((c) =>
                  c.kind === 'multiple_matches'
                    ? `客户 ${c.customer_id} 同时命中既有客户 ${c.conflict_with.join('、')}`
                    : `客户 ${c.customer_id} 没有电话/邮箱，无法判重`,
                )
                .join('；')}
            />
          )}
        </>
      )}
    </Modal>
  )
}
