import QtQuick
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "lib/Dana.js" as Dana

// The quick-ask card: the conversation Dana remembers now, what she waits
// for, a screenshot waiting to be sent, the input line and quiet hints.
// Ask.qml puts it on screen; tests/harness renders it offscreen. Every key
// is handled here so the card behaves the same wherever it is shown.
BorderSurface {
  id: root

  property var svc: null
  property real conversationMax: Style.space(600)
  property bool showHelp: false
  property var picked: []

  signal dismissRequested()
  signal captureRequested()

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }

  readonly property bool ready: !!svc && svc.ready
  readonly property var pending: svc ? svc.pending : null
  readonly property var live: svc ? svc.live : null
  readonly property var turns: svc ? svc.turns : []
  readonly property bool working: !!svc && svc.working
  readonly property bool hasConversation: turns.length > 0 || live !== null

  readonly property color fg: Commons.Color.menu.text
  readonly property color accent: Commons.Color.accent
  readonly property color urgent: Commons.Color.urgent
  readonly property int smallRadius: Math.min(4, Style.cornerRadius)

  implicitHeight: body.implicitHeight + contentTopInset + contentBottomInset
  radius: Style.cornerRadius
  color: Commons.Color.menu.background
  borderSpec: Border.surfaceSpec("menu", "border", Commons.Color.menu.border, Math.max(1, Style.space(2)))
  padding: Style.spacing.panelPadding

  // Called when the card shows: focus the input and land on the newest turn.
  function focusInput() {
    root.showHelp = false
    root.picked = []
    input.forceActiveFocus()
    input.cursorPosition = input.length
    conversation.stick = true
    conversation.toEnd()
  }

  function setText(text) { input.text = text }

  function submit() {
    var text = input.text
    if (text.trim() || root.svc.attachmentData) {
      if (root.svc.send(text)) { input.text = ""; conversation.stick = true }
      return
    }
    if (!root.pending) return
    if (root.pending.kind === "approval") root.svc.respond("approve")
    else if (root.pending.kind === "iterations") root.svc.respond("extend_iterations")
    else if (root.pending.kind === "selection" && root.pending.multiple && root.picked.length) root.svc.respond(root.picked)
  }

  function decline() {
    if (!root.pending) return
    if (root.pending.kind === "approval") root.svc.respond("deny")
    else if (root.pending.kind === "iterations") root.svc.respond("decline_iterations")
  }

  function pick(index) {
    var p = root.pending
    if (!p || p.kind !== "selection" || index < 0 || index >= p.options.length) return
    if (!p.multiple) { root.svc.respond([index]); return }
    var next = root.picked.slice()
    var at = next.indexOf(index)
    if (at >= 0) next.splice(at, 1)
    else next.push(index)
    root.picked = next
  }

  function lastAnswer() {
    if (root.live && root.live.answer) return root.live.answer
    for (var i = root.turns.length - 1; i >= 0; i--) if (root.turns[i].answer) return root.turns[i].answer
    return ""
  }

  function openLink(link) {
    var safe = Dana.safeLink(link)
    if (safe) Qt.openUrlExternally(safe)
  }

  onPendingChanged: root.picked = []

  MouseArea { anchors.fill: parent; onClicked: input.forceActiveFocus() }

  Column {
    id: body
    x: root.contentLeftInset
    y: root.contentTopInset
    width: root.width - root.contentLeftInset - root.contentRightInset
    spacing: Style.space(12)

    // ---------- conversation ----------
    Flickable {
      id: conversation
      width: parent.width
      visible: root.hasConversation && !root.showHelp && root.ready
      readonly property real maxHeight: root.conversationMax
      height: visible ? Math.min(turnColumn.implicitHeight, maxHeight) : 0
      contentHeight: turnColumn.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      property bool stick: true

      function toEnd() { contentY = Math.max(0, contentHeight - height) }
      onMovementEnded: stick = atYEnd
      onContentHeightChanged: if (stick) Qt.callLater(toEnd)
      onHeightChanged: if (stick) Qt.callLater(toEnd)

      Column {
        id: turnColumn
        width: conversation.width
        spacing: Style.space(18)

        Repeater {
          model: root.turns
          delegate: TurnView {
            width: turnColumn.width
            prompt: modelData.prompt
            answer: modelData.answer
            images: modelData.images
          }
        }

        TurnView {
          visible: root.live !== null
          width: turnColumn.width
          prompt: root.live ? root.live.prompt : ""
          answer: root.live ? root.live.answer : ""
          images: root.live && root.live.image ? 1 : 0
          liveTurn: true
          activity: root.live ? root.live.activity : ""
          failure: root.live && root.live.error
            ? (root.live.error === "cancelled" ? strings.t("ask.stopped") : root.live.error) : ""
        }
      }
    }

    // ---------- shortcuts ----------
    Column {
      visible: root.showHelp
      width: parent.width
      spacing: Style.space(6)

      PlainText {
        text: strings.t("help.title")
        font.pixelSize: Style.font.title
        font.bold: true
      }

      Repeater {
        model: [
          ["↵", "help.send"], ["Shift+↵", "help.newline"], ["Esc", "help.close"],
          ["↵ / Ctrl+D", "help.approve"], ["1–9", "help.pick"], ["Ctrl+N", "help.newTopic"],
          ["Ctrl+Shift+S", "help.screenshot"], ["Ctrl+V", "help.paste"], ["Ctrl+.", "help.stop"],
          ["Ctrl+Shift+C", "help.copy"], ["Ctrl+O", "help.web"], ["↑", "help.recall"], ["F1", "help.help"]
        ]
        delegate: Row {
          spacing: Style.space(12)
          PlainText {
            width: Style.space(120)
            text: modelData[0]
            opacity: 0.7
          }
          PlainText {
            width: body.width - Style.space(132)
            text: strings.t(modelData[1])
            wrapMode: Text.Wrap
          }
        }
      }
    }

    // ---------- what Dana waits for ----------
    Rectangle {
      id: pendingCard
      visible: root.ready && root.pending !== null && !root.showHelp
      width: parent.width
      height: visible ? pendingColumn.implicitHeight + Style.space(24) : 0
      radius: root.smallRadius
      color: Style.selectedAccentFill
      border.width: Math.max(1, Style.space(1))
      border.color: Commons.Util.alpha(root.accent, 0.5)

      Column {
        id: pendingColumn
        x: Style.space(12)
        y: Style.space(12)
        width: parent.width - Style.space(24)
        spacing: Style.space(8)

        PlainText {
          text: !root.pending ? "" : root.pending.kind === "approval" ? strings.t("approval.caption")
            : root.pending.kind === "selection" ? strings.t("selection.caption") : strings.t("iterations.caption")
          font.pixelSize: Style.font.caption
          font.bold: true
          font.letterSpacing: 1.2
          color: root.accent
        }

        PlainText {
          width: parent.width
          visible: text !== ""
          wrapMode: Text.Wrap
          font.pixelSize: Style.font.title
          font.bold: true
          text: !root.pending ? "" : root.pending.kind === "approval" ? (root.pending.title || strings.t("approval.unknown"))
            : root.pending.kind === "selection" ? root.pending.prompt : root.pending.summary
        }

        Repeater {
          model: root.pending && root.pending.kind === "approval" ? root.pending.lines : []
          delegate: PlainText {
            width: pendingColumn.width
            text: modelData
            wrapMode: Text.Wrap
            opacity: 0.8
            maximumLineCount: 6
            elide: Text.ElideRight
          }
        }

        Repeater {
          model: root.pending && root.pending.kind === "selection" ? root.pending.options : []
          delegate: Button {
            width: pendingColumn.width
            leftAlign: true
            bordered: true
            selected: root.picked.indexOf(index) >= 0
            text: (index + 1) + "   " + modelData
            onClicked: root.pick(index)
          }
        }

        Row {
          spacing: Style.space(8)
          visible: root.pending !== null && (root.pending.kind !== "selection" || root.pending.multiple)

          Button {
            bordered: true
            selected: true
            text: !root.pending ? "" : root.pending.kind === "approval" ? strings.t("approval.approve")
              : root.pending.kind === "iterations" ? strings.t("iterations.continue") : strings.t("selection.done")
            onClicked: root.submit()
          }
          Button {
            visible: root.pending !== null && root.pending.kind !== "selection"
            bordered: true
            text: root.pending && root.pending.kind === "iterations" ? strings.t("iterations.stop") : strings.t("approval.deny")
            onClicked: root.decline()
          }
        }
      }
    }

    // ---------- setup ----------
    Column {
      visible: !!root.svc && !root.ready
      width: parent.width
      spacing: Style.space(8)

      PlainText {
        text: strings.t("setup.title")
        font.pixelSize: Style.font.title
        font.bold: true
      }
      PlainText {
        width: parent.width
        wrapMode: Text.Wrap
        opacity: 0.8
        text: !root.svc ? "" : root.svc.setupProblem === "url" ? strings.t("setup.badUrl").arg(root.svc.envPath)
          : root.svc.setupProblem === "token" ? strings.t("setup.badToken").arg(root.svc.envPath)
          : strings.t("setup.body").arg(root.svc.envPath)
      }
      PlainText {
        text: "DANA_URL=https://dana.example.com\nDANA_TOKEN=…"
        opacity: 0.6
      }
    }

    // ---------- problem ----------
    PlainText {
      visible: text !== "" && root.ready
      width: parent.width
      wrapMode: Text.Wrap
      color: root.urgent
      text: root.svc && root.svc.errorKind ? strings.t("error." + root.svc.errorKind) : ""
    }

    // ---------- attached image ----------
    Row {
      visible: !!root.svc && root.svc.attachmentPath !== ""
      spacing: Style.space(10)

      Rectangle {
        width: thumb.paintedWidth + 2
        height: Style.space(56)
        radius: root.smallRadius
        color: "transparent"
        border.width: 1
        border.color: Commons.Util.alpha(root.fg, 0.3)
        Image {
          id: thumb
          anchors.centerIn: parent
          height: parent.height - 2
          width: Style.space(160)
          fillMode: Image.PreserveAspectFit
          asynchronous: true
          sourceSize.height: Style.space(112)
          source: root.svc && root.svc.attachmentPath ? "file://" + root.svc.attachmentPath : ""
        }
      }
      PlainText {
        anchors.verticalCenter: parent.verticalCenter
        text: strings.t("ask.attached")
        opacity: 0.7
        font.pixelSize: Style.font.bodySmall
      }
    }

    // ---------- input ----------
    Item {
      visible: root.ready
      width: parent.width
      height: Math.max(promptGlyph.implicitHeight, input.implicitHeight)

      PlainText {
        id: promptGlyph
        anchors.left: parent.left
        anchors.top: parent.top
        text: String.fromCodePoint(0xF0AE2)
        color: root.accent
        font.pixelSize: Style.font.heading
        opacity: root.working ? 0.45 : 1

        SequentialAnimation on opacity {
          running: root.working && Style.duration(900) > 0
          loops: Animation.Infinite
          alwaysRunToEnd: true
          NumberAnimation { to: 0.35; duration: Style.duration(900); easing.type: Easing.InOutSine }
          NumberAnimation { to: 1; duration: Style.duration(900); easing.type: Easing.InOutSine }
        }
      }

      TextEdit {
        id: input
        anchors.left: promptGlyph.right
        anchors.leftMargin: Style.space(12)
        anchors.right: parent.right
        anchors.top: parent.top
        textFormat: TextEdit.PlainText
        wrapMode: TextEdit.Wrap
        color: root.fg
        selectionColor: Style.selectionFill
        selectedTextColor: root.fg
        font.family: Style.font.family
        font.pixelSize: Style.font.heading
        focus: true
        persistentSelection: false

        PlainText {
          anchors.fill: parent
          visible: input.length === 0
          opacity: 0.45
          font.pixelSize: Style.font.heading
          elide: Text.ElideRight
          text: !root.pending ? (root.svc && root.svc.attachmentPath ? strings.t("ask.placeholderImage") : strings.t("ask.placeholder"))
            : root.pending.kind === "approval" ? strings.t("ask.placeholderApproval")
            : root.pending.kind === "selection" ? strings.t("ask.placeholderSelection")
            : strings.t("ask.placeholderIterations")
        }

        Keys.priority: Keys.BeforeItem
        Keys.onPressed: function(event) {
          var ctrl = (event.modifiers & Qt.ControlModifier) !== 0
          var shift = (event.modifiers & Qt.ShiftModifier) !== 0
          var empty = input.length === 0
          if (event.key === Qt.Key_Escape) {
            if (root.showHelp) root.showHelp = false
            else if (!empty) input.text = ""
            else root.dismissRequested()
            event.accepted = true
          } else if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && !shift) {
            root.submit()
            event.accepted = true
          } else if (event.key === Qt.Key_F1 || (empty && event.text === "?")) {
            root.showHelp = !root.showHelp
            event.accepted = true
          } else if (ctrl && event.key === Qt.Key_D) {
            root.decline()
            event.accepted = true
          } else if (ctrl && event.key === Qt.Key_N) {
            root.svc.newTopic()
            event.accepted = true
          } else if (ctrl && event.key === Qt.Key_Period) {
            root.svc.stop()
            event.accepted = true
          } else if (ctrl && shift && event.key === Qt.Key_C) {
            root.svc.copy(root.lastAnswer())
            event.accepted = true
          } else if (ctrl && shift && event.key === Qt.Key_S) {
            root.captureRequested()
            event.accepted = true
          } else if (ctrl && event.key === Qt.Key_O) {
            root.svc.openWeb()
            event.accepted = true
          } else if (ctrl && event.key === Qt.Key_V) {
            // An image on the clipboard is attached; text still pastes below.
            root.svc.pasteImage()
          } else if (empty && event.key === Qt.Key_Backspace && root.svc.attachmentPath) {
            root.svc.clearAttachment()
            event.accepted = true
          } else if (empty && event.key === Qt.Key_Up && root.svc.lastPrompt) {
            input.text = root.svc.lastPrompt
            input.cursorPosition = input.length
            event.accepted = true
          } else if (empty && root.pending && root.pending.kind === "selection" && event.text >= "1" && event.text <= "9") {
            root.pick(parseInt(event.text, 10) - 1)
            event.accepted = true
          } else if (event.key === Qt.Key_PageUp || event.key === Qt.Key_PageDown) {
            var step = conversation.height * 0.8 * (event.key === Qt.Key_PageUp ? -1 : 1)
            conversation.contentY = Math.max(0, Math.min(conversation.contentHeight - conversation.height, conversation.contentY + step))
            conversation.stick = conversation.atYEnd
            event.accepted = true
          }
        }
      }
    }

    // ---------- footer ----------
    Item {
      width: parent.width
      height: footerLeft.implicitHeight
      visible: root.ready

      PlainText {
        id: footerLeft
        anchors.left: parent.left
        anchors.right: footerRight.left
        anchors.rightMargin: Style.space(12)
        elide: Text.ElideRight
        font.pixelSize: Style.font.caption
        opacity: 0.5
        text: !root.svc ? "" : root.working ? strings.t("footer.working")
          : root.svc.capturing ? strings.t("footer.capturing")
          : !root.svc.isThread ? strings.t("footer.side").arg(root.svc.title || strings.t("footer.sideFallback"))
          : root.turns.length > 0 ? strings.t("footer.continues")
          : strings.t("footer.fresh")
      }
      PlainText {
        id: footerRight
        anchors.right: parent.right
        font.pixelSize: Style.font.caption
        opacity: 0.5
        text: strings.t("footer.help")
      }
    }
  }

  // One exchange: the question, faded, and the answer as sanitized Markdown.
  component TurnView: Column {
    id: turn
    property string prompt: ""
    property string answer: ""
    property int images: 0
    property bool liveTurn: false
    property string activity: ""
    property string failure: ""
    spacing: Style.space(6)

    PlainText {
      visible: turn.prompt !== "" || turn.images > 0
      width: turn.width
      wrapMode: Text.Wrap
      opacity: 0.55
      maximumLineCount: 4
      elide: Text.ElideRight
      text: (turn.images > 0 ? String.fromCodePoint(0xF02E9) + "  " : "") + turn.prompt
    }

    Text {
      visible: turn.answer !== ""
      width: turn.width
      textFormat: Text.MarkdownText
      wrapMode: Text.Wrap
      color: root.fg
      linkColor: root.accent
      font.family: Style.font.family
      font.pixelSize: Style.font.subtitle
      lineHeight: 1.15
      text: Dana.safeMarkdown(turn.answer)
      onLinkActivated: function(link) { root.openLink(link) }
      HoverHandler { cursorShape: parent.hoveredLink ? Qt.PointingHandCursor : Qt.ArrowCursor }
    }

    PlainText {
      visible: turn.liveTurn && turn.failure === "" && (turn.activity !== "" || (turn.answer === "" && root.working))
      text: turn.activity !== "" ? strings.t("ask.activity").arg(turn.activity) : strings.t("ask.thinking")
      opacity: 0.6
      font.pixelSize: Style.font.bodySmall
    }

    PlainText {
      visible: turn.failure !== ""
      width: turn.width
      wrapMode: Text.Wrap
      color: root.urgent
      text: turn.failure
    }
  }

  component PlainText: Text {
    textFormat: Text.PlainText
    color: root.fg
    font.family: Style.font.family
    font.pixelSize: Style.font.body
  }
}
