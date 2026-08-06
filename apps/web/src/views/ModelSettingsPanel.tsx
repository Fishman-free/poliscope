/** 右侧栏永久模型设置面板。
 *
 * 保存于服务器（`/api/settings/model`），创建任务时自动生效——表单里不再
 * 有任务级模型设置。API Key 永不回显：GET 只返回 `has_api_key`，所以这里的
 * 密码框永远留空，靠徽章告诉你「已配置」；再次保存时留空即保留旧 Key，
 * 「清除已保存 Key」是唯一移除方式（避免一次误保存把凭据冲掉）。
 */

import { useEffect, useState } from "react";

import { fetchModelSettings, saveModelSettings } from "../api/client";
import { Badge, Panel } from "../components/primitives";

import "./ModelSettingsPanel.css";

export function ModelSettingsPanel() {
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [modelName, setModelName] = useState("");
  const [hasApiKey, setHasApiKey] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchModelSettings()
      .then((settings) => {
        if (cancelled) return;
        setBaseUrl(settings.base_url ?? "");
        setModelName(settings.model_name ?? "");
        setHasApiKey(settings.has_api_key);
        setLoaded(true);
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function save() {
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const result = await saveModelSettings({
        base_url: baseUrl.trim() || null,
        // 留空 = 保留服务器上已存的 Key；非空才覆盖。
        api_key: apiKey.trim() || null,
        model_name: modelName.trim() || null,
      });
      setApiKey("");
      setHasApiKey(result.has_api_key);
      setSaved(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  }

  async function clearKey() {
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const result = await saveModelSettings({ clear_api_key: true });
      setHasApiKey(result.has_api_key);
      setApiKey("");
      setSaved(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Panel
      title="模型设置"
      subtitle="保存一次，之后创建的任务自动使用"
    >
      {/* 加载失败也必须可见：错误的、没加载出来的面板不能伪装成「没有
          面板」。错误信息渲染在表单外层，任何状态都看得到。 */}
      {error ? (
        <p className="settings__error" role="alert">
          {error}
        </p>
      ) : null}
      {!loaded && !error ? (
        <p className="settings__loading">正在载入设置…</p>
      ) : null}
      {!loaded ? null : (
        <div className="settings__form">
          <label className="settings__field">
            Base URL（例如 https://api.deepseek.com）
            <input
              type="url"
              placeholder="https://…"
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              disabled={saving}
              spellCheck={false}
            />
          </label>
          <label className="settings__field">
            API Key
            <span className="settings__key-row">
              <input
                type="password"
                autoComplete="off"
                placeholder={hasApiKey ? "留空保留已配置的 Key" : "sk-…"}
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                disabled={saving}
                spellCheck={false}
              />
              {hasApiKey ? <Badge tone="admitted">已配置</Badge> : null}
            </span>
          </label>
          <label className="settings__field">
            模型名（可留空，默认 deepseek-v4-flash）
            <input
              placeholder="deepseek-v4-flash"
              value={modelName}
              onChange={(event) => setModelName(event.target.value)}
              disabled={saving}
              spellCheck={false}
            />
          </label>

          <div className="settings__actions">
            <button
              type="button"
              className="button button--primary"
              onClick={save}
              disabled={saving}
            >
              {saving ? "保存中…" : "保存设置"}
            </button>
            {hasApiKey ? (
              <button
                type="button"
                className="button"
                onClick={clearKey}
                disabled={saving}
              >
                清除 Key
              </button>
            ) : null}
          </div>

          {saved ? <p className="settings__ok">已保存 ✓</p> : null}
          <p className="settings__note">
            API Key 只存服务器、任何页面都不会回显；不设置则使用部署方配置的系统默认模型。
          </p>
        </div>
      )}
    </Panel>
  );
}
