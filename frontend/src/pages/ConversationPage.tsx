import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { createConversation, getConversation, getConversations, sendConversationMessage } from "../features/conversations/api";

export function ConversationPage() {
  const { client } = useAuth(); const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null); const [content, setContent] = useState(""); const messageEnd = useRef<HTMLDivElement>(null);
  const conversations = useQuery({ queryKey: ["conversations"], queryFn: () => getConversations(client) });
  useEffect(() => { if (!selectedId && conversations.data?.length) setSelectedId(conversations.data[0].id); }, [conversations.data, selectedId]);
  const detail = useQuery({ queryKey: ["conversation", selectedId], queryFn: () => getConversation(client, selectedId!), enabled: Boolean(selectedId) });
  const create = useMutation({ mutationFn: () => createConversation(client), onSuccess: async (item) => { setSelectedId(item.id); await queryClient.invalidateQueries({ queryKey: ["conversations"] }); } });
  const send = useMutation({ mutationFn: (text: string) => sendConversationMessage(client, selectedId!, text), onSuccess: async () => { setContent(""); await Promise.all([queryClient.invalidateQueries({ queryKey: ["conversation", selectedId] }), queryClient.invalidateQueries({ queryKey: ["conversations"] })]); } });
  useEffect(() => { messageEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [detail.data?.messages.length, send.isPending]);
  function submit(event: FormEvent) { event.preventDefault(); const text = content.trim(); if (text && selectedId && !send.isPending) send.mutate(text); }
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }
  const prompts = ["checkout 现在有正在处理的 Incident 吗？", "请排查 checkout 的最新告警", "腾讯云发布后错误率升高应该怎么排查？"];
  return <main>
    <div className="page-heading"><div><div className="eyebrow">CONVERSATIONAL OPS</div><h1>对话助手</h1><p>用自然语言查询事件、发起调查或检索已审核的运维知识。</p></div><button onClick={() => create.mutate()} disabled={create.isPending}>＋ 新建对话</button></div>
    <div className="safety-note"><span>安全边界</span> 对话可以查询、调查和生成计划，但不能审批或执行恢复操作。</div>
    <div className="conversation-layout"><aside className="conversation-list"><div className="conversation-list-title">最近对话</div>{conversations.data?.map((item) => <button className={item.id === selectedId ? "conversation-item active" : "conversation-item"} key={item.id} onClick={() => setSelectedId(item.id)}><strong>{item.title}</strong><small>{item.last_intent === "INCIDENT" ? "事件处理" : item.last_intent === "KNOWLEDGE" ? "知识问答" : "尚未分类"}</small></button>)}{!conversations.isLoading && conversations.data?.length === 0 && <p className="muted conversation-empty">还没有对话</p>}</aside>
      <section className="chat-panel">{!selectedId && <div className="chat-welcome"><div className="assistant-orb">✦</div><h2>我是你的 OnCall 协作助手</h2><p>先新建一个对话。我会把问题路由到知识检索或真实 Incident 工作流。</p><button onClick={() => create.mutate()} disabled={create.isPending}>开始第一次对话</button></div>}{selectedId && detail.data?.messages.length === 0 && <div className="chat-welcome"><div className="assistant-orb">✦</div><h2>从一个真实问题开始</h2><p>可以查询状态、要求调查，或询问发布故障的处理方法。</p><div className="prompt-grid">{prompts.map((prompt) => <button className="prompt-card" key={prompt} onClick={() => setContent(prompt)}>{prompt}<span>↗</span></button>)}</div></div>}<div className="messages">{detail.data?.messages.map((message) => { const incidentPath = typeof message.metadata.incident_path === "string" ? message.metadata.incident_path : null; return <article key={message.id} className={`message ${message.role}`}><div className="message-meta"><span>{message.role === "user" ? "你" : "✦ OnCall Agent"}</span>{message.intent && <span className="badge">{message.intent} · {message.action}</span>}</div><p>{message.content}</p>{incidentPath && <Link className="incident-link" to={incidentPath}>打开关联 Incident →</Link>}{message.citations.length > 0 && <details><summary>查看 {message.citations.length} 条知识引用</summary><ul>{message.citations.map((citation, index) => <li key={index}>{String(citation.title)} · {String(citation.section)}</li>)}</ul></details>}</article>; })}{send.isPending && <article className="message assistant typing"><span /><span /><span /></article>}<div ref={messageEnd} /></div>
        {send.error && <div className="error-banner">{send.error.message}</div>}<form className="chat-form" onSubmit={submit}><textarea value={content} onChange={(event) => setContent(event.target.value)} onKeyDown={handleKeyDown} placeholder="输入问题，Enter 发送，Shift + Enter 换行…" maxLength={8000} disabled={!selectedId || send.isPending} /><button aria-label="发送消息" disabled={!selectedId || !content.trim() || send.isPending}>发送 ↗</button></form>
      </section></div>
  </main>;
}
