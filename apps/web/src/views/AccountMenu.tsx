/** 顶栏账号入口：头像（或用户名首字母圆形）+ 账户菜单。
 *
 * 菜单项：「账号设置」（打开 AccountSettings 弹层）与「退出登录」。浮层
 * 交互沿用 SessionHistory 的 popover 标准：点击外部 / Escape 关闭。
 * 头像字节经 API 拉取为 blob URL，登出/卸载时 revoke。
 */

import { useEffect, useRef, useState } from "react";

import { fetchAvatarBlob, logout } from "../api/client";
import { t } from "../i18n";

import { AccountSettings } from "./AccountSettings";
import "./AccountMenu.css";

export function AccountMenu({
  username,
  onSignedOut,
}: {
  username: string;
  onSignedOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [avatarUrl, setAvatarUrl] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let objectUrl: string | null = null;
    fetchAvatarBlob()
      .then((blob) => {
        if (blob) {
          objectUrl = URL.createObjectURL(blob);
          setAvatarUrl(objectUrl);
        }
      })
      .catch(() => {
        /* 无头像或拉取失败时回落为用户名首字母 */
      });
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, []);

  // 外部点击 / Escape 关闭菜单。
  useEffect(() => {
    if (!open) return;
    function onDown(event: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function signOut() {
    setOpen(false);
    await logout();
    onSignedOut();
  }

  return (
    <div className="account-menu" ref={rootRef}>
      <button
        type="button"
        className="account-menu__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        title={username}
      >
        {avatarUrl ? (
          <img
            className="account-menu__avatar-img"
            src={avatarUrl}
            alt={username}
          />
        ) : (
          <span className="account-menu__avatar-fallback">
            {username.charAt(0).toUpperCase()}
          </span>
        )}
      </button>

      {open ? (
        <div className="account-menu__popover" role="menu">
          <div className="account-menu__head">
            <span className="account-menu__name">{username}</span>
          </div>
          <button
            type="button"
            className="account-menu__item"
            onClick={() => {
              setOpen(false);
              setSettingsOpen(true);
            }}
          >
            {t("账号设置")}
          </button>
          <button
            type="button"
            className="account-menu__item account-menu__item--danger"
            onClick={signOut}
          >
            {t("退出登录")}
          </button>
        </div>
      ) : null}

      {settingsOpen ? (
        <AccountSettings
          username={username}
          onClose={() => setSettingsOpen(false)}
          onAvatarChanged={() => {
            // 重新拉取头像（上传成功后 blob URL 已失效）。
            fetchAvatarBlob()
              .then((blob) => {
                if (blob) {
                  setAvatarUrl(URL.createObjectURL(blob));
                }
              })
              .catch(() => {});
          }}
        />
      ) : null}
    </div>
  );
}
