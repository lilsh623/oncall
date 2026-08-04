import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { createConversation, getConversation, getConversations, sendConversationMessage } from "../features/conversations/api";

export function ConversationPage() {
  const { client } = useAuth(); const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null); const [content, setContent] = useState("");
  const conversations = useQuery({ queryKey: ["conversations"], queryFn: () => getConversations(client) });
  useEffect(() => { if (!selectedId && conversations.data?.length) setSelectedId(conversations.data[0].id); }, [conversations.data, selectedId]);
  const detail = useQuery({ queryKey: ["conversation", selectedId], queryFn: () => getConversation(client, selectedId!), enabled: Boolean(selectedId) });
  const create = useMutation({ mutationFn: () => createConversation(client), onSuccess: async (item) => { setSelectedId(item.id); await queryClient.invalidateQueries({ queryKey: ["conversations"] }); } });
  const send = useMutation({ mutationFn: (text: string) => sendConversationMessage(client, selectedId!, text), onSuccess: async () => { setContent(""); await Promise.all([queryClient.invalidateQueries({ queryKey: ["conversation", selectedId] }), queryClient.invalidateQueries({ queryKey: ["conversations"] })]); } });
  function submit(event: FormEvent) { event.preventDefault(); const text = content.trim(); if (text && selectedId && !send.isPending) send.mutate(text); }
  return <main>
    <div className="page-heading"><div><h1>对话助手</h1><p>知识问答与故障处理统一入口；审批和恢复执行仅允许在 Incident 页面完成。</p></div><button onClick={() => create.mutate()} disabled={create.isPending}>新建对话</button></div>
    <div className="conversation-layout"><aside className="conversation-list">{conversations.data?.map((item) => <button className={item.id === selectedId ? "conversation-item active" : "conversation-item"} key={item.id} onClick={() => setSelectedId(item.id)}><strong>{item.title}</strong><small>{item.last_intent ?? "尚未分类"}</small></button>)}</aside>
      <section className="chat-panel">{!selectedId && <div className="empty-state">新建一个对话开始使用 OnCall 助手。</div>}<div className="messages">{detail.data?.messages.map((message) => { const incidentPath = typeof message.metadata.incident_path === "string" ? message.metadata.incident_path : null; return <article key={message.id} className={`message ${message.role}`}><div className="message-meta"><span>{message.role === "user" ? "你" : "OnCall Agent"}</span>{message.intent && <span className="badge">{message.intent} · {message.action}</span>}</div><p>{message.content}</p>{incidentPath && <Link to={incidentPath}>打开 Incident 详情</Link>}{message.citations.length > 0 && <details><summary>查看 {message.citations.length} 条知识引用</summary><ul>{message.citations.map((citation, index) => <li key={index}>{String(citation.title)} · {String(citation.section)}</li>)}</ul></details>}</article>; })}{send.isPending && <article className="message assistant"><p>正在分析并路由请求…</p></article>}</div>
        {send.error && <p className="error">{send.error.message}</p>}<form className="chat-form" onSubmit={submit}><textarea value={content} onChange={(event) => setContent(event.target.value)} placeholder="询问处置知识，或查询、排查具体 Incident…" maxLength={8000} disabled={!selectedId || send.isPending} /><button disabled={!selectedId || !content.trim() || send.isPending}>发送</button></form>
      </section></div>
  </main>;
}
