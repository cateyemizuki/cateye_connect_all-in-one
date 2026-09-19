/**
 * maibot-headless runner 插件（DSH 侧）
 *
 * 替代 dsh-headless 自带的 runner，实现：
 *   1. 会话复用：按工作区名称派生固定 sessionId，
 *      已持久化的会话自动 resume（同一工作区 = 同一个持续对话）；
 *   2. 会话标题固定为工作区名称（Web UI 对话条目直接显示名称）；
 *   3. 会话 cwd = 进程工作目录（deepseek 根），并确保名为 "maibot" 的
 *      workspace 存在（所有 maibot 会话自动归入该分组，显示于 Web UI）；
 *   4. 完成后打印最终助手文本（不含思维链）并退出。
 *
 * 纯 ESM、零外部依赖（所有需要用到的逻辑均内联）。
 */

import { createHash, randomUUID } from 'node:crypto'

export const name = 'maibot-runner'
export const inject = ['agentDefaultModel', 'agents', 'sessions', 'sessionPersistence', 'sessionTitle', 'workspaceRegistry']

/** 进程级输出与退出（测试可替换）。 */
export const internals = {
  stdout: process.stdout,
  stderr: process.stderr,
}

/** 按工作区名称派生固定会话 id：session-maibot-<sha1 前 16 位>。 */
export function sessionIdOf(workspace) {
  const digest = createHash('sha1').update('maibot:' + workspace, 'utf8').digest('hex').slice(0, 16)
  return 'session-maibot-' + digest
}

/**
 * 内联 installModelSelection：把选中的 provider/model 注入
 * system-prompt/assemble 与 agent/request（等价于 @deepseek-ai/dsh-agent 的导出）。
 */
function installModelSelection(agentCtx, selection) {
  const disposeAssembly = agentCtx.on('system-prompt/assemble', async (_assembly, _context, next) => {
    const selected = selection.current
    const assembled = await next()
    selection.assembled = selected
    if (selected === undefined) return assembled
    return {
      ...assembled,
      variables: {
        ...assembled.variables,
        provider: selected.provider,
        model: selected.model,
      },
    }
  })
  const disposeRequest = agentCtx.on('agent/request', async (_payload, next) => {
    const resolved = await next()
    const selected = selection.assembled
    if (selected === undefined) return resolved
    const { reasoningEffort: _inheritedEffort, ...withoutInheritedEffort } = resolved
    return {
      ...withoutInheritedEffort,
      provider: selected.provider,
      model: selected.model,
      ...(selected.reasoningEffort === undefined ? {} : { reasoningEffort: selected.reasoningEffort }),
    }
  })
  return () => {
    disposeAssembly()
    disposeRequest()
  }
}

/** 聚合最后一次助手文本与回合结果（等价于 dsh-headless 的 summarize）。 */
function summarize(events, firstSeq) {
  let started = false
  let text = ''
  let reason
  for (const event of events) {
    if (event.seq < firstSeq) continue
    if (event.type === 'turn/start') {
      started = true
      continue
    }
    if (!started) continue
    if (event.type === 'assistant/message') {
      const joined = event.data.message.content
        .filter((block) => block.type === 'text')
        .map((block) => block.text)
        .join('')
      if (joined !== '') text = joined
    }
    if (event.type === 'turn/end') reason = event.data.reason
  }
  return { text, reason }
}

async function run(ctx, config, io) {
  await ctx.get('loader')?.await()
  const agents = ctx.get('agents')
  const defaultModel = ctx.get('agentDefaultModel')
  const sessions = ctx.get('sessions')
  const persistence = ctx.get('sessionPersistence')
  const sessionTitle = ctx.get('sessionTitle')
  const workspaceRegistry = ctx.get('workspaceRegistry')
  if (!agents || !defaultModel || !sessions || !persistence || !sessionTitle || !workspaceRegistry) {
    throw new Error('maibot-runner: 缺少必需服务（agents/sessions/sessionPersistence/sessionTitle/workspaceRegistry）')
  }

  const { workspace, task } = config
  if (!workspace || !task) throw new Error('maibot-runner: 缺少工作区名称或对话内容')

  const sessionId = sessionIdOf(workspace)
  const selection = defaultModel.currentSelection()
  const setup = (agentCtx) => {
    const selected = { current: selection, assembled: undefined }
    installModelSelection(agentCtx, selected)
  }

  // 确保名为 "maibot" 的 workspace 存在（path = 进程工作目录，即 deepseek 根）。
  // 会话 cwd 与 workspace path 相同 → 所有 maibot 会话自动归入该分组。
  try {
    await workspaceRegistry.create(process.cwd(), 'maibot')
  } catch (error) {
    io.stderr.write(`dsh: maibot workspace 创建失败: ${error instanceof Error ? error.message : String(error)}\n`)
  }

  // 会话复用：已持久化 → resume；否则新建。
  let exists = false
  try {
    const headers = await persistence.list()
    exists = headers.some((header) => header.id === sessionId)
  } catch {
    exists = false
  }

  let handle
  if (exists) {
    handle = await agents.resume({
      resumeSessionId: sessionId,
      agentOptions: { provider: selection.provider, model: selection.model },
      setup,
    })
  } else {
    handle = await agents.create({
      sessionId,
      meta: { cwd: process.cwd() },
      agentOptions: { provider: selection.provider, model: selection.model },
      setup,
    })
  }
  const agent = handle.agent
  await agent.whenIdle()

  // 标题固定为工作区名称（钉住，Web UI 对话条目直接显示）。
  try {
    sessionTitle.rename(agent.session, workspace)
  } catch {
    try {
      agent.session.append('session/title', {
        title: workspace,
        messageSeqs: [],
        source: { kind: 'user' },
      })
    } catch { /* 标题失败不阻塞任务 */ }
  }

  const firstSeq = agent.session.seq
  agent.followup({
    id: randomUUID(),
    role: 'user',
    content: [{ type: 'text', text: task }],
    source: { kind: 'user' },
  })
  await agent.whenIdle()
  await sessions.flush(agent.session)

  const outcome = summarize(agent.session.events, firstSeq)
  io.stdout.write(outcome.text + '\n')
  if (outcome.reason?.kind === 'error') {
    io.stderr.write(`dsh: ${outcome.reason.error.code}: ${outcome.reason.error.message}\n`)
  }
  io.exit(outcome.reason?.kind === 'completed' ? 0 : 1)
}

export function apply(ctx, config) {
  const exit = ctx.get('appExit')
  if (!exit) {
    throw new Error('maibot-runner: launcher 必须提供 ctx.appExit')
  }
  const io = { stdout: internals.stdout, stderr: internals.stderr, exit }
  void run(ctx, config, io).catch((error) => {
    io.stderr.write(`dsh: ${error instanceof Error ? error.message : String(error)}\n`)
    io.exit(1)
  })
}
