import {
  Action,
  ActionPanel,
  Alert,
  Color,
  confirmAlert,
  getPreferenceValues,
  Icon,
  Keyboard,
  List,
  showToast,
  Toast,
  useNavigation,
} from "@raycast/api";
import { useCachedPromise, useCachedState } from "@raycast/utils";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "../lib/api";
import {
  applyEvent,
  humanToolName,
  LiveRun,
  newLiveRun,
  newSessionId,
  Pending,
  pendingFromState,
  transcript,
  Turn,
  turnMarkdown,
  turnsFromMessages,
} from "../lib/chat";
import { errorMessage } from "../lib/format";
import type { ModelProfile, SessionState, StreamFrame } from "../lib/types";
import { ChatHistory } from "./ChatHistory";
import { SelectionForm } from "./SelectionForm";
import { mod } from "../lib/shortcuts";

const FALLBACK_MODELS: ModelProfile[] = [
  { id: "fast", label: "Fast", description: "Quickest replies", model: "", shows_reasoning: false },
  { id: "balanced", label: "Balanced", description: "Thinks before acting", model: "", shows_reasoning: true },
  { id: "deep", label: "Deep", description: "Most capable", model: "", shows_reasoning: true },
];

/** Re-attach this many times in a row at most when a stream closes while the run still goes. */
const MAX_REATTACH = 5;

interface ChatProps {
  /** Show this session instead of the remembered "current" chat (used when opened from history). */
  sessionId?: string;
  title?: string;
  /** Send this message as soon as the chat opens, in a fresh session. */
  initialPrompt?: string;
}

export function Chat({ sessionId: fixedSessionId, title, initialPrompt }: ChatProps) {
  const prefs = getPreferenceValues<Preferences>();
  const showSteps = prefs.showReasoning !== false;
  const [currentSession, setCurrentSession] = useCachedState<string>("current-session", newSessionId());
  const [sessionId, setSessionId] = useState<string>(() => {
    if (fixedSessionId) return fixedSessionId;
    if (initialPrompt?.trim()) return newSessionId();
    return currentSession;
  });
  const [model, setModel] = useCachedState<string>("model", prefs.defaultModel || "fast");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState<Pending>(null);
  const [live, setLive] = useState<LiveRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [searchText, setSearchText] = useState("");
  const [selectedId, setSelectedId] = useState<string | undefined>();
  const { push, pop } = useNavigation();

  const liveRef = useRef<LiveRun | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const sessionRef = useRef(sessionId);
  const reattachRef = useRef(0);
  const sentInitial = useRef(false);

  const { data: catalog } = useCachedPromise(api.getModels, [], { keepPreviousData: true });
  const models = catalog?.models?.length ? catalog.models : FALLBACK_MODELS;

  // Remember the chat that Ask Dana should reopen next time.
  useEffect(() => {
    sessionRef.current = sessionId;
    if (!fixedSessionId) setCurrentSession(sessionId);
  }, [sessionId, fixedSessionId]);

  // Stop reading streams when the view closes — the run itself keeps going on the server.
  useEffect(() => () => abortRef.current?.abort(), []);

  const setLiveRun = (run: LiveRun | null) => {
    liveRef.current = run;
    setLive(run);
  };

  /** Apply the authoritative session state (end of every stream, and on open). */
  const applyState = useCallback((state: SessionState, opts: { follow: boolean }) => {
    if (state.session_id !== sessionRef.current) return;
    const nextTurns = turnsFromMessages(state.messages ?? []);
    // History doesn't keep the model's reasoning; carry it over from the live run.
    const finished = liveRef.current?.turn;
    const last = nextTurns[nextTurns.length - 1];
    if (finished?.reasoning && last && !last.reasoning && (!finished.prompt || finished.prompt === last.prompt)) {
      last.reasoning = finished.reasoning;
    }
    if (state.status === "running" && state.active_run && opts.follow && reattachRef.current < MAX_REATTACH) {
      reattachRef.current += 1;
      follow(state, nextTurns);
      return;
    }
    reattachRef.current = 0;
    setTurns(nextTurns);
    setPending(pendingFromState(state, nextTurns));
    setLiveRun(null);
    setSelectedId(nextTurns.length ? nextTurns[nextTurns.length - 1].id : undefined);
  }, []);

  /** Pipe a stream into the live turn until the closing `state` frame. */
  const stream = useCallback(
    async (run: LiveRun, start: (opts: api.StreamOptions) => Promise<void>, onBusy?: () => void): Promise<void> => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      const ownSession = sessionRef.current;
      setLiveRun(run);
      setPending(null);
      setSelectedId(run.turn.id);
      let gotState = false;
      try {
        await start({
          signal: controller.signal,
          onFrame: (frame: StreamFrame) => {
            if (controller.signal.aborted || ownSession !== sessionRef.current) return;
            if (frame.type === "state") {
              gotState = true;
              applyState(frame.state, { follow: true });
              return;
            }
            if (liveRef.current) setLiveRun(applyEvent(liveRef.current, frame));
          },
        });
        if (!gotState && !controller.signal.aborted) {
          // Connection dropped before the end: pick up where we left off.
          const state = await api.getSession(ownSession);
          if (state) applyState(state, { follow: true });
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof api.ApiError && error.status === 409 && onBusy) {
          onBusy();
          return;
        }
        const failed = liveRef.current;
        setLiveRun(null);
        if (failed) {
          setTurns((prev) => [...prev, { ...failed.turn, live: false, error: errorMessage(error) }]);
        }
        await showToast({ style: Toast.Style.Failure, title: "Dana couldn't answer", message: errorMessage(error) });
      }
    },
    [applyState],
  );

  /** Attach to a run that is already going (opened mid-run, 409, or a capped stream). */
  const follow = useCallback(
    (state: SessionState, stateTurns: Turn[]) => {
      const turnsCopy = [...stateTurns];
      // The running turn's user message is usually already in history without an answer.
      const tail = turnsCopy[turnsCopy.length - 1];
      let prompt = "";
      if (tail && !tail.answer && tail.prompt) {
        turnsCopy.pop();
        prompt = tail.prompt;
      }
      setTurns(turnsCopy);
      const runId = state.active_run?.run_id;
      void stream(newLiveRun(prompt), (opts) => api.attach(state.session_id, runId, 0, opts));
    },
    [stream],
  );

  // Load the conversation when the session changes.
  useEffect(() => {
    let cancelled = false;
    abortRef.current?.abort();
    setTurns([]);
    setPending(null);
    setLiveRun(null);
    setLoading(true);
    (async () => {
      try {
        const state = await api.getSession(sessionId);
        if (cancelled) return;
        if (state) applyState(state, { follow: true });
      } catch (error) {
        if (!cancelled) {
          await showToast({ style: Toast.Style.Failure, title: "Couldn't load chat", message: errorMessage(error) });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const send = useCallback(
    async (text: string) => {
      const message = text.trim();
      if (!message) return;
      setSearchText("");
      const run = newLiveRun(message);
      await stream(
        run,
        (opts) => api.sendMessage(sessionRef.current, message, model, opts),
        async () => {
          await showToast({
            style: Toast.Style.Failure,
            title: "Dana is still busy",
            message: "Showing the request she's working on. Send again when she's done.",
          });
          setSearchText(message);
          const state = await api.getSession(sessionRef.current);
          if (state) applyState(state, { follow: true });
        },
      );
    },
    [model, stream, applyState],
  );

  // Launch argument / fallback text.
  useEffect(() => {
    if (!initialPrompt?.trim() || sentInitial.current || loading) return;
    sentInitial.current = true;
    void send(initialPrompt);
  }, [initialPrompt, loading, send]);

  const resume = useCallback(
    (action: api.ResumeAction | { select: number[] }) => {
      const lastTurn = turns[turns.length - 1];
      const run = newLiveRun("");
      if (lastTurn) {
        // Continue the same exchange rather than adding a new one.
        setTurns(turns.slice(0, -1));
        run.turn = { ...lastTurn, live: true, error: undefined };
        run.committed = lastTurn.answer;
        run.turn.tools = lastTurn.tools.map((t) =>
          t.state === "needs_approval" ? { ...t, state: action === "deny" ? "failed" : "running" } : t,
        );
      }
      void stream(run, (opts) => api.resume(sessionRef.current, action, opts));
    },
    [turns, stream],
  );

  const stop = useCallback(async () => {
    try {
      await api.cancelRun(sessionRef.current);
      await showToast({ style: Toast.Style.Success, title: "Stopped" });
    } catch (error) {
      await showToast({ style: Toast.Style.Failure, title: "Couldn't stop Dana", message: errorMessage(error) });
    }
  }, []);

  const newChat = useCallback(() => {
    if (fixedSessionId) {
      pop();
      return;
    }
    setSessionId(newSessionId());
    setSearchText("");
  }, [fixedSessionId, pop]);

  const deleteChat = useCallback(async () => {
    const ok = await confirmAlert({
      title: "Delete this chat?",
      message: "The conversation is removed from Dana for every device.",
      icon: { source: Icon.Trash, tintColor: Color.Red },
      primaryAction: { title: "Delete", style: Alert.ActionStyle.Destructive },
    });
    if (!ok) return;
    try {
      await api.deleteSession(sessionRef.current);
      await showToast({ style: Toast.Style.Success, title: "Chat deleted" });
      newChat();
    } catch (error) {
      await showToast({ style: Toast.Style.Failure, title: "Couldn't delete chat", message: errorMessage(error) });
    }
  }, [newChat]);

  const allTurns = useMemo(() => (live ? [...turns, live.turn] : turns), [turns, live]);
  const activePending = live ? live.pending : pending;
  const latestId = allTurns[allTurns.length - 1]?.id;
  const busy = Boolean(live && live.status === "running" && !live.pending);

  const modelDropdown = (
    <List.Dropdown tooltip="Model" value={model} onChange={setModel}>
      {models.map((m) => (
        <List.Dropdown.Item key={m.id} value={m.id} title={m.label} icon={m.shows_reasoning ? Icon.Stars : Icon.Bolt} />
      ))}
    </List.Dropdown>
  );

  const sessionActions = (
    <>
      <ActionPanel.Section>
        <Action title="New Chat" icon={Icon.PlusCircle} shortcut={Keyboard.Shortcut.Common.New} onAction={newChat} />
        {!fixedSessionId && (
          <Action
            title="Search Chats"
            icon={Icon.MagnifyingGlass}
            shortcut={Keyboard.Shortcut.Common.ToggleQuickLook}
            onAction={() =>
              push(
                <ChatHistory
                  currentSessionId={sessionId}
                  onSelect={(id) => {
                    setSessionId(id);
                    pop();
                  }}
                />,
              )
            }
          />
        )}
        <Action.OpenInBrowser
          title="Open Dana on the Web"
          url={api.serverUrl()}
          shortcut={Keyboard.Shortcut.Common.Open}
        />
      </ActionPanel.Section>
      {turns.length > 0 && (
        <ActionPanel.Section>
          <Action
            title="Delete Chat"
            icon={Icon.Trash}
            style={Action.Style.Destructive}
            shortcut={Keyboard.Shortcut.Common.RemoveAll}
            onAction={deleteChat}
          />
        </ActionPanel.Section>
      )}
    </>
  );

  const pendingActions = (turn: Turn) => {
    if (turn.id !== latestId || !activePending) return null;
    if (activePending.kind === "approval") {
      const name = humanToolName(activePending.tool.name);
      return (
        <ActionPanel.Section title={`Dana wants to run ${name}`}>
          <Action
            title="Approve"
            icon={{ source: Icon.Check, tintColor: Color.Green }}
            onAction={() => resume("approve")}
          />
          <Action
            title="Deny"
            icon={{ source: Icon.XMarkCircle, tintColor: Color.Red }}
            shortcut={Keyboard.Shortcut.Common.Remove}
            onAction={() => resume("deny")}
          />
        </ActionPanel.Section>
      );
    }
    if (activePending.kind === "selection") {
      const { request } = activePending;
      if (request.multiple) {
        return (
          <Action
            title="Choose Options…"
            icon={Icon.CheckList}
            onAction={() =>
              push(<SelectionForm request={request} onSubmit={(indices) => resume({ select: indices })} />)
            }
          />
        );
      }
      return (
        <ActionPanel.Section title={request.prompt || "Choose an option"}>
          {request.options.map((option, index) => (
            <Action
              key={`${index}-${option}`}
              title={option}
              icon={Icon.Circle}
              onAction={() => resume({ select: [index] })}
            />
          ))}
        </ActionPanel.Section>
      );
    }
    return (
      <ActionPanel.Section>
        <Action title="Let Dana Continue" icon={Icon.Play} onAction={() => resume("extend_iterations")} />
        <Action
          title="Stop Here"
          icon={Icon.Stop}
          shortcut={Keyboard.Shortcut.Common.Remove}
          onAction={() => resume("decline_iterations")}
        />
      </ActionPanel.Section>
    );
  };

  const sendAction = searchText.trim() ? (
    <Action title="Send Message" icon={Icon.Message} onAction={() => send(searchText)} />
  ) : null;

  const placeholder = busy
    ? "Dana is working…"
    : activePending?.kind === "approval"
      ? "Approve or deny with the actions, or type a new message…"
      : turns.length
        ? "Reply to Dana…"
        : "Ask Dana anything…";

  return (
    <List
      isLoading={loading || busy}
      isShowingDetail={allTurns.length > 0}
      filtering={false}
      searchText={searchText}
      onSearchTextChange={setSearchText}
      searchBarPlaceholder={placeholder}
      searchBarAccessory={modelDropdown}
      selectedItemId={selectedId}
      onSelectionChange={(id) => id && setSelectedId(id)}
      navigationTitle={fixedSessionId ? title || "Chat" : undefined}
      throttle={false}
    >
      {allTurns.length === 0 ? (
        <List.EmptyView
          icon={{ source: "icon.png" }}
          title={loading ? "Loading chat…" : "Ask Dana anything"}
          description={
            loading
              ? undefined
              : "Type a message and press ↵. Dana can check the house, your calendar, WhatsApp, tasks and more."
          }
          actions={
            <ActionPanel>
              {sendAction}
              {sessionActions}
            </ActionPanel>
          }
        />
      ) : (
        [...allTurns].reverse().map((turn) => {
          const isLatest = turn.id === latestId;
          const turnPending = isLatest ? activePending : null;
          const accessories: List.Item.Accessory[] = [];
          if (turn.live && !turnPending) accessories.push({ icon: Icon.CircleProgress, tooltip: "Working" });
          if (turnPending?.kind === "approval")
            accessories.push({ icon: { source: Icon.Shield, tintColor: Color.Orange }, tooltip: "Needs approval" });
          if (turnPending?.kind === "selection")
            accessories.push({ icon: { source: Icon.QuestionMarkCircle, tintColor: Color.Blue }, tooltip: "Choose" });
          if (turn.error)
            accessories.push({ icon: { source: Icon.Warning, tintColor: Color.Red }, tooltip: turn.error });
          return (
            <List.Item
              key={turn.id}
              id={turn.id}
              title={turn.prompt || "Dana"}
              icon={turn.prompt ? Icon.Person : { source: "icon.png" }}
              accessories={accessories}
              detail={<List.Item.Detail markdown={turnMarkdown(turn, { showSteps, pending: turnPending })} />}
              actions={
                <ActionPanel>
                  {sendAction}
                  {pendingActions(turn)}
                  {busy && isLatest && (
                    <Action
                      title="Stop Dana"
                      icon={Icon.Stop}
                      shortcut={Keyboard.Shortcut.Common.Pin}
                      onAction={stop}
                    />
                  )}
                  <ActionPanel.Section>
                    {turn.answer && <Action.CopyToClipboard title="Copy Answer" content={turn.answer} />}
                    {turn.answer && (
                      <Action.Paste title="Paste Answer" content={turn.answer} shortcut={mod("v", true)} />
                    )}
                    {turn.prompt && (
                      <Action
                        title="Edit Question"
                        icon={Icon.Pencil}
                        shortcut={Keyboard.Shortcut.Common.Edit}
                        onAction={() => setSearchText(turn.prompt)}
                      />
                    )}
                    <Action.CopyToClipboard
                      title="Copy Conversation"
                      content={transcript(allTurns)}
                      shortcut={Keyboard.Shortcut.Common.Copy}
                    />
                  </ActionPanel.Section>
                  {sessionActions}
                </ActionPanel>
              }
            />
          );
        })
      )}
    </List>
  );
}
