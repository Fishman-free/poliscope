/** 右侧栏 Skills 面板：已下载的技能 + 勾选启用 + 新增。
 *
 * 技能是研究者从 GitHub 添加的技能仓库（SKILL.md）。勾选 = 启用：启用后
 * 新建任务默认携带，worker 会把它注入议会 prompt 作为「研究者提供的技能
 * 指令」（非正式证据）。面板下方随时可以输入新的 GitHub URL 下载入列表。
 */

import { useCallback, useEffect, useState } from "react";

import {
  addSkill,
  deleteSkill,
  fetchSkills,
  setSkillEnabled,
} from "../api/client";
import type { SkillSummary } from "../api/types";
import { Empty, Panel, Spinner } from "../components/primitives";

import "./SkillsPanel.css";

export function SkillsPanel({
  onChanged,
}: {
  /** Fired after add/toggle/delete so NewTaskView's skill select can refresh. */
  onChanged?: () => void;
}) {
  const [skills, setSkills] = useState<SkillSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newUrl, setNewUrl] = useState("");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setSkills(await fetchSkills());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function add() {
    if (!newUrl.trim() || adding) return;
    setAdding(true);
    setAddError(null);
    try {
      await addSkill(newUrl.trim());
      setNewUrl("");
      await refresh();
      onChanged?.();
    } catch (cause) {
      setAddError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdding(false);
    }
  }

  async function toggle(skill: SkillSummary) {
    try {
      await setSkillEnabled(skill.id, !skill.enabled);
      await refresh();
      onChanged?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function remove(skill: SkillSummary) {
    try {
      await deleteSkill(skill.id);
      await refresh();
      onChanged?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  return (
    <Panel
      title="Skills"
      subtitle="GitHub 技能，勾选后注入议会"
    >
      {error ? (
        <p className="skills__error" role="alert">
          {error}
        </p>
      ) : null}
      {skills === null ? (
        <Spinner label="正在加载 Skills…" />
      ) : skills.length === 0 ? (
        <Empty>还没有技能。在下方输入 GitHub 仓库地址下载第一个。</Empty>
      ) : (
        <ul className="skills__list">
          {skills.map((skill) => (
            <li key={skill.id} className="skills__row">
              <label className="skills__row-main">
                <input
                  type="checkbox"
                  checked={skill.enabled}
                  onChange={() => toggle(skill)}
                />
                <span className="skills__info">
                  <span className="skills__name">{skill.name}</span>
                  <span className="skills__url">{skill.github_url}</span>
                </span>
              </label>
              <button
                type="button"
                className="button button--small"
                onClick={() => remove(skill)}
                title="删除此技能"
              >
                删除
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="skills__add">
        <p className="skills__add-hint">
          新增技能：输入 GitHub 仓库地址（如 https://github.com/owner/skill-name）
        </p>
        <input
          placeholder="https://github.com/…"
          value={newUrl}
          onChange={(event) => setNewUrl(event.target.value)}
          disabled={adding}
          spellCheck={false}
        />
        <button
          type="button"
          className="button"
          onClick={add}
          disabled={adding || !newUrl.trim()}
        >
          {adding ? "下载中…" : "下载并添加"}
        </button>
        {addError ? (
          <p className="skills__error" role="alert">
            {addError}
          </p>
        ) : null}
      </div>
    </Panel>
  );
}
