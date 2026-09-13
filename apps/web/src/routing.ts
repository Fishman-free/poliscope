/** 顶层路径判定。
 *
 * 只有一个消费者会显得小题大做，但「什么是一个分享链接」被两处各写了一遍
 * 正是它此前失效的原因：`main.tsx` 决定挂载落地页还是工作台，`App` 决定
 * 是否跳过鉴权门。只认 `/workspace` 的那次分发让分享链接落到落地页，接收
 * 者看到一排「进入工作台 / 登录」——而分享链接的价值恰恰在于不需要账号。
 * 把形状收敛到一处，两边的判断就不可能再分叉。 */

/** `/shared/{token}`：公开只读分享，不需要登录。 */
const SHARED_PATH = /^\/shared\/([^/]+)\/?$/;

/** 从路径里取出分享令牌；不是分享链接则返回 null。 */
export function shareTokenFromPath(pathname: string): string | null {
  const matched = SHARED_PATH.exec(pathname);
  return matched ? decodeURIComponent(matched[1] ?? "") : null;
}
