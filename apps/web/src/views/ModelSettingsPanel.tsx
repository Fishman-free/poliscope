/** 右侧栏永久模型设置面板。
 *
 * 保存于服务器（`/api/settings/model`），创建任务时自动生效——表单里不再
 * 有任务级模型设置。API Key 永不回显：GET 只返回 `has_api_key`，所以这里的
 * 密码框永远留空，靠徽章告诉你「已配置」；再次保存时留空即保留旧 Key，
 * 「清除已保存 Key」是唯一移除方式（避免一次误保存把凭据冲掉）。
 *
 * **连接门控。** 发生过一次真实事故：研究者把 DeepSeek 的「控制台门户」当成
 * API 端点保存，整个议会全部缺席。所以这里的两条规矩是硬性的：
 * 1. 保存前必须先「测试连接」——后端会用表单当前值对真实端点做一次最小
 *    调用，只有探测成功才会落库（后端同样强制，前端只是不让按钮可用）；
 * 2. 输入任何字段都会使已验证状态失效，必须重新测试。
 * 测试结果里若带了 `corrected_base_url`（门户地址被自动纠正成 API 地址），
 * 表单会自动采用纠正后的值，并显示一句说明。
 */

import { useEffect, useState } from "react";

import {
  fetchModelSettings,
  saveModelSettings,
  testModelConnection,
} from "../api/client";
import type { ModelTestResult } from "../api/types";
import { Badge, Panel } from "../components/primitives";
import { t } from "../i18n";

import "./ModelSettingsPanel.css";

export function ModelSettingsPanel() {
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [modelName, setModelName] = useState("");
  const [hasApiKey, setHasApiKey] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  // 连接已验证（最近一次测试成功）。任何字段改动都会使它失效。
  const [verified, setVerified] = useState(false);
  const [testResult, setTestResult] = useState<ModelTestResult | null>(null);
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

  /** 任何字段改动都会使「已验证」失效：保存的必须是被测过的那组值。 */
  function invalidateConnection() {
    setVerified(false);
    setTestResult(null);
    setSaved(false);
  }

  async function testConnection() {
    setTesting(true);
    setError(null);
    setSaved(false);
    try {
      const result = await testModelConnection({
        base_url: baseUrl.trim() || null,
        // 留空 = 使用服务器上已存的 Key 来测（后端语义与保存一致）。
        api_key: apiKey.trim() || null,
        model_name: modelName.trim() || null,
      });
      setTestResult(result);
      if (result.ok) {
        setVerified(true);
        if (result.corrected_base_url) {
          // 门户地址被纠正成 API 地址：采用纠正后的值，保存的就是被测过的。
          setBaseUrl(result.corrected_base_url);
        }
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setTesting(false);
    }
  }

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
      setVerified(false);
      setTestResult(null);
      setSaved(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  }

  const busy = saving || testing;
  const canTest = Boolean(baseUrl.trim()) && !busy;
  // 保存必须建立在本组值通过连接测试之上——这是硬门控。
  const canSave = verified && !busy;

  return (
    <Panel
      title={t("模型设置")}
      subtitle={t("保存一次，之后创建的任务自动使用")}
    >
      {/* 加载失败也必须可见：错误的、没加载出来的面板不能伪装成「没有
          面板」。错误信息渲染在表单外层，任何状态都看得到。 */}
      {error ? (
        <p className="settings__error" role="alert">
          {error}
        </p>
      ) : null}
      {!loaded && !error ? (
        <p className="settings__loading">{t("正在载入设置…")}</p>
      ) : null}
      {!loaded ? null : (
        <div className="settings__form">
          <label className="settings__field">
            {t("Base URL（例如 https://api.deepseek.com）")}
            <input
              type="url"
              placeholder="https://…"
              value={baseUrl}
              onChange={(event) => {
                setBaseUrl(event.target.value);
                invalidateConnection();
              }}
              disabled={busy}
              spellCheck={false}
            />
          </label>
          <label className="settings__field">
            API Key
            <span className="settings__key-row">
              <input
                type="password"
                autoComplete="off"
                placeholder={hasApiKey ? t("留空保留已配置的 Key") : "sk-…"}
                value={apiKey}
                onChange={(event) => {
                  setApiKey(event.target.value);
                  invalidateConnection();
                }}
                disabled={busy}
                spellCheck={false}
              />
              {hasApiKey ? <Badge tone="admitted">{t("已配置")}</Badge> : null}
            </span>
          </label>
          <label className="settings__field">
            {t("模型名（可留空，默认 deepseek-v4-flash）")}
            <input
              placeholder="deepseek-v4-flash"
              value={modelName}
              onChange={(event) => {
                setModelName(event.target.value);
                invalidateConnection();
              }}
              disabled={busy}
              spellCheck={false}
            />
          </label>

          {/* 连接测试结果：成功给延迟与纠正说明，失败给出可操作的原因。 */}
          {testResult ? (
            <div
              className={
                testResult.ok ? "settings__test-ok" : "settings__test-fail"
              }
              role={testResult.ok ? "status" : "alert"}
            >
              {testResult.ok ? "✓ " : "✗ "}
              {testResult.message}
              {/* 纠正提示只在成功时显示：失败时地址并未被采用（字段保持
                  用户输入），说「已自动采用」就是撒谎。 */}
              {testResult.ok && testResult.correction ? (
                <span className="settings__correction">
                  {" "}
                  {t("（{0}，已自动采用）", testResult.correction)}
                </span>
              ) : null}
            </div>
          ) : null}

          <div className="settings__actions">
            <button
              type="button"
              className={verified ? "button button--primary" : "button"}
              onClick={testConnection}
              disabled={!canTest}
              title={
                baseUrl.trim()
                  ? t("用当前填写的内容连接真实端点验证")
                  : t("请先填写 Base URL")
              }
            >
              {testing ? t("测试中…") : verified ? t("重新测试") : t("测试连接")}
            </button>
            <button
              type="button"
              className="button button--primary"
              onClick={save}
              disabled={!canSave}
              title={
                verified
                  ? t("保存设置")
                  : t("只有连接测试通过后才能保存（修改任意字段后需重新测试）")
              }
            >
              {saving ? t("保存中…") : t("保存设置")}
            </button>
            {hasApiKey ? (
              <button
                type="button"
                className="button"
                onClick={clearKey}
                disabled={busy}
              >
                {t("清除 Key")}
              </button>
            ) : null}
          </div>

          {!verified ? (
            <p className="settings__gate-hint">
              {t("修改后需先「测试连接」通过，才能保存设置。")}
            </p>
          ) : null}
          {saved ? <p className="settings__ok">{t("已保存 ✓")}</p> : null}
          <p className="settings__note">
            {t(
              "API Key 只存服务器、任何页面都不会回显；不设置则使用部署方配置的系统默认模型。",
            )}
          </p>
        </div>
      )}
    </Panel>
  );
}
