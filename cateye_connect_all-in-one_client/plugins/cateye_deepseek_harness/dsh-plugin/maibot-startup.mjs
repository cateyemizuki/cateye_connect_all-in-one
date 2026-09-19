/**
 * maibot-headless startup 插件（DSH 侧）
 *
 * 替代 dsh-headless 自带的 startup：解析自定义参数
 *   --maibot-workspace <工作区名称>
 * 并把剩余参数拼接为任务文本，发布 maibotStartup 服务。
 *
 * 通过 --patch 覆盖层挂载（由本地客户端自动生成 patch 文件）。
 */

export const name = 'maibot-startup'
export const inject = ['cmdlineArgs']

export function apply(ctx) {
  const args = ctx.cmdlineArgs?.get?.() ?? []
  let workspace = ''
  const rest = []
  for (let i = 0; i < args.length; i++) {
    const token = args[i]
    if (token === '--maibot-workspace' && i + 1 < args.length) {
      workspace = String(args[i + 1])
      i++
    } else {
      rest.push(token)
    }
  }
  const task = rest.join(' ').trim()
  if (!workspace || !task) {
    ctx.logger?.error('maibot: 用法: --maibot-workspace <工作区名称> <对话内容>')
    ctx.get('appExit')?.(1)
    return
  }
  ctx.provide('maibotStartup', { workspace, task })
}
