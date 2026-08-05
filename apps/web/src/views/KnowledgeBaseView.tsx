/** Knowledge Base: the researcher's long-term memory.
 *
 * Documents uploaded here are parsed to text, stored across tasks, and
 * linkable to a task at creation (NewTaskView's knowledge-base select) -- the
 * council then treats them as Level A user-provided sources and keyword-
 * searches them during acquisition. This view manages the collection:
 * create/delete bases, upload/delete documents, preview parsed text.
 */

import { useCallback, useEffect, useState } from "react";

import {
  addTextDocument,
  createKnowledgeBase,
  deleteKnowledgeBase,
  deleteKnowledgeDocument,
  fetchKnowledgeBase,
  fetchKnowledgeBases,
  fetchKnowledgeDocumentText,
  uploadKnowledgeDocument,
} from "../api/client";
import type {
  KnowledgeBaseDetail,
  KnowledgeBaseSummary,
  KnowledgeDocumentDetail,
} from "../api/types";
import { Badge, Empty, Panel, Spinner } from "../components/primitives";

import "./KnowledgeBaseView.css";

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

const CONTENT_TYPE_LABELS: Record<string, string> = {
  "application/pdf": "PDF",
  "text/plain": "文本",
  "text/markdown": "Markdown",
  "text/csv": "CSV",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PPT",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Excel",
};

export function KnowledgeBaseView({ active = true }: { active?: boolean }) {
  const [bases, setBases] = useState<KnowledgeBaseSummary[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<KnowledgeBaseDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  // 粘贴文本：不用文件，直接入库成为知识库文档（与上传同等检索与 Level A 待遇）。
  const [pastedTitle, setPastedTitle] = useState("");
  const [pastedContent, setPastedContent] = useState("");
  const [pasting, setPasting] = useState(false);
  const [pasteError, setPasteError] = useState<string | null>(null);
  const [previews, setPreviews] = useState<Record<string, KnowledgeDocumentDetail>>({});
  const [previewErrors, setPreviewErrors] = useState<Record<string, string>>({});

  const refreshList = useCallback(async () => {
    try {
      setBases(await fetchKnowledgeBases());
    } catch {
      setBases([]);
    }
  }, []);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  // 从「新建任务」切回来时刷新：两个主页视图同时挂载、各自持有列表状态，
  // 另一边的内联建库/删除不会通知这里，可见时才拉最新（首次进入也会拉到）。
  useEffect(() => {
    if (active) void refreshList();
  }, [active, refreshList]);

  useEffect(() => {
    if (selectedId === null) {
      setDetail(null);
      setDetailError(null);
      return;
    }
    let cancelled = false;
    setDetailError(null);
    fetchKnowledgeBase(selectedId)
      .then((value) => {
        if (!cancelled) setDetail(value);
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setDetailError(cause instanceof Error ? cause.message : String(cause));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  async function create() {
    if (!newName.trim() || creating) return;
    setCreating(true);
    setCreateError(null);
    try {
      const created = await createKnowledgeBase(newName.trim(), newDescription.trim());
      setNewName("");
      setNewDescription("");
      setSelectedId(created.id);
      await refreshList();
    } catch (cause) {
      setCreateError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setCreating(false);
    }
  }

  const ACCEPTED_EXTENSIONS = [".pdf", ".txt", ".md", ".csv", ".docx", ".pptx", ".xlsx"];

  async function handleFiles(files: FileList | null) {
    if (!selectedId || !files || uploading) return;
    setUploading(true);
    setUploadError(null);
    for (const file of Array.from(files)) {
      const lower = file.name.toLowerCase();
      const looksSupported = ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
      if (!looksSupported) {
        setUploadError(`「${file.name}」格式不支持，已跳过（支持 PDF/TXT/MD/CSV/DOCX/PPTX/XLSX）`);
        continue;
      }
      try {
        await uploadKnowledgeDocument(selectedId, file);
      } catch (cause) {
        setUploadError(
          `「${file.name}」上传失败：${cause instanceof Error ? cause.message : String(cause)}`,
        );
      }
    }
    const fresh = await fetchKnowledgeBase(selectedId);
    setDetail(fresh);
    setUploading(false);
  }

  async function pasteDocument() {
    if (!selectedId || pasting || !pastedTitle.trim() || !pastedContent.trim()) {
      return;
    }
    setPasting(true);
    setPasteError(null);
    try {
      await addTextDocument(selectedId, pastedTitle.trim(), pastedContent);
      setPastedTitle("");
      setPastedContent("");
      setDetail(await fetchKnowledgeBase(selectedId));
    } catch (cause) {
      setPasteError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPasting(false);
    }
  }

  async function removeDocument(docId: string) {
    if (!selectedId) return;
    try {
      await deleteKnowledgeDocument(selectedId, docId);
      setDetail(await fetchKnowledgeBase(selectedId));
    } catch (cause) {
      setDetailError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function removeBase() {
    if (!selectedId) return;
    try {
      await deleteKnowledgeBase(selectedId);
      setSelectedId(null);
      await refreshList();
    } catch (cause) {
      setDetailError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function togglePreview(docId: string) {
    if (previews[docId]) {
      setPreviews((prev) => {
        const next = { ...prev };
        delete next[docId];
        return next;
      });
      return;
    }
    if (!selectedId) return;
    try {
      const text = await fetchKnowledgeDocumentText(selectedId, docId);
      setPreviews((prev) => ({ ...prev, [docId]: text }));
    } catch (cause) {
      setPreviewErrors((prev) => ({
        ...prev,
        [docId]: cause instanceof Error ? cause.message : String(cause),
      }));
    }
  }

  if (bases === null) {
    return (
      <Panel title="知识库" subtitle="长期记忆与检索">
        <Spinner label="正在加载知识库…" />
      </Panel>
    );
  }

  return (
    <div className="knowledge__layout">
      <section className="knowledge__list">
        <h3 className="knowledge__section-title">知识库</h3>
        {bases.length === 0 ? (
          <Empty>还没有知识库。先创建一个，再上传 PDF 文档。</Empty>
        ) : (
          <ul className="knowledge__bases">
            {bases.map((kb) => (
              <li key={kb.id}>
                <button
                  type="button"
                  className={
                    "knowledge__base-row" + (kb.id === selectedId ? " knowledge__base-row--active" : "")
                  }
                  onClick={() => setSelectedId(kb.id)}
                >
                  <span className="knowledge__base-name">{kb.name}</span>
                  <span className="knowledge__base-meta">
                    {kb.document_count} 篇文档
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className="knowledge__create">
          <input
            placeholder="新知识库名称"
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
          />
          <input
            placeholder="描述（可选）"
            value={newDescription}
            onChange={(event) => setNewDescription(event.target.value)}
          />
          <button
            type="button"
            className="button"
            onClick={create}
            disabled={creating || !newName.trim()}
          >
            {creating ? "创建中…" : "创建知识库"}
          </button>
          {createError ? (
            <p className="knowledge__error" role="alert">
              {createError}
            </p>
          ) : null}
        </div>
      </section>

      <section className="knowledge__detail">
        {detailError ? (
          <p className="knowledge__error" role="alert">
            {detailError}
          </p>
        ) : null}
        {detail === null ? (
          <Empty>在左侧选择一个知识库查看文档。</Empty>
        ) : (
          <>
            <header className="knowledge__header">
              <div>
                <h3 className="knowledge__section-title">{detail.name}</h3>
                {detail.description ? (
                  <p className="knowledge__description">{detail.description}</p>
                ) : null}
                <p className="knowledge__meta">
                  共 {detail.documents.length} 篇文档 · 中文检索为子串匹配
                </p>
              </div>
              <button
                type="button"
                className="button"
                onClick={removeBase}
                disabled={detail.documents.length > 0}
                title={
                  detail.documents.length > 0
                    ? "请先删除知识库内的全部文档"
                    : "删除此知识库"
                }
              >
                删除
              </button>
            </header>

            <div className="knowledge__upload">
              <p className="knowledge__upload-hint">
                上传文件（PDF / TXT / MD / CSV / DOCX / PPTX / XLSX，单个不超过 20 MB；
                老版 .doc/.ppt/.xls 请先另存为新格式）
              </p>
              <input
                type="file"
                accept=".pdf,.txt,.md,.csv,.docx,.pptx,.xlsx"
                multiple
                disabled={uploading}
                onChange={(event) => handleFiles(event.target.files)}
              />
              {uploadError ? (
                <p className="knowledge__error" role="alert">
                  {uploadError}
                </p>
              ) : null}
            </div>

            <div className="knowledge__paste">
              <p className="knowledge__upload-hint">
                或者直接粘贴文本作为知识库文档（笔记、网页摘录、报告片段均可）
              </p>
              <input
                placeholder="文档标题"
                value={pastedTitle}
                onChange={(event) => setPastedTitle(event.target.value)}
                disabled={pasting}
              />
              <textarea
                placeholder="把内容粘贴到这里…"
                rows={4}
                value={pastedContent}
                onChange={(event) => setPastedContent(event.target.value)}
                disabled={pasting}
              />
              <button
                type="button"
                className="button"
                onClick={pasteDocument}
                disabled={pasting || !pastedTitle.trim() || !pastedContent.trim()}
              >
                {pasting ? "保存中…" : "加入知识库"}
              </button>
              {pasteError ? (
                <p className="knowledge__error" role="alert">
                  {pasteError}
                </p>
              ) : null}
            </div>

            {detail.documents.length === 0 ? (
              <Empty>这个知识库还没有文档，上传文件或粘贴文本后即可在任务中使用。</Empty>
            ) : (
              <ul className="knowledge__documents">
                {detail.documents.map((doc) => (
                  <li key={doc.document_id} className="knowledge__document">
                    <div className="knowledge__document-row">
                      <div className="knowledge__document-info">
                        <span className="knowledge__document-title">{doc.title}</span>
                        <span className="knowledge__document-meta">
                          {CONTENT_TYPE_LABELS[doc.content_type] ?? doc.content_type} ·{" "}
                          {doc.page_count} 页/块 · {formatBytes(doc.size_bytes)}
                        </span>
                      </div>
                      <div className="knowledge__document-actions">
                        <button
                          type="button"
                          className="button button--small"
                          onClick={() => togglePreview(doc.document_id)}
                        >
                          {previews[doc.document_id] ? "收起" : "预览"}
                        </button>
                        <button
                          type="button"
                          className="button button--small"
                          onClick={() => removeDocument(doc.document_id)}
                        >
                          删除
                        </button>
                      </div>
                    </div>
                    {previewErrors[doc.document_id] ? (
                      <p className="knowledge__error" role="alert">
                        {previewErrors[doc.document_id]}
                      </p>
                    ) : null}
                    {previews[doc.document_id] ? (
                      <details className="knowledge__preview" open>
                        <summary>
                          解析文本
                          {previews[doc.document_id]?.truncated ? (
                            <Badge tone="unknown">已截断（仅前 20000 字符）</Badge>
                          ) : null}
                        </summary>
                        <pre className="knowledge__preview-text">
                          {previews[doc.document_id]?.text ?? ""}
                        </pre>
                      </details>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>
    </div>
  );
}
