import QtQuick
import Quickshell
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "lib/Dana.js" as Dana

// Dana in the bar: one glyph. A number beside it when Now has cards that need
// a decision; the glyph pulses while Dana works on something you asked here
// and turns the bar's alert colour when she is waiting for your OK.
//
// Click: the Now panel (the cards, each with its actions). Right click: the
// quick-ask overlay (also Super+D). Middle click: refresh.
//
// The service is a singleton shared with the overlay; the shell hands plugin
// settings to this widget, so the widget pushes them across.
Panel {
  id: root
  moduleName: "dana"
  manageIpc: false

  // `omarchy-shell dana-panel toggle` opens this panel from a key or a
  // script; the plugin id itself routes to the quick-ask overlay.
  ShellIpc {
    target: "dana-panel"
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): void { if (root.svc) root.svc.refreshNow() }
  }

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }

  property var svc: null
  function lookupService() {
    if (root.svc || !root.bar || !root.bar.shell || typeof root.bar.shell.serviceFor !== "function") return
    root.svc = root.bar.shell.serviceFor("dana")
  }
  Timer {
    interval: 1000
    repeat: true
    running: !root.svc
    triggeredOnStart: true
    onTriggered: root.lookupService()
  }

  function pushSettings() { if (root.svc) root.svc.applySettings(root.settings) }
  onSettingsChanged: pushSettings()
  onSvcChanged: pushSettings()

  readonly property bool ready: !!svc && svc.ready
  readonly property bool convoRow: !!svc && (svc.working || svc.pending !== null)
  // The approval the conversation row already shows isn't listed twice.
  readonly property var items: !svc || !svc.now ? [] : svc.now.items.filter(function(item) {
    return !(root.convoRow && svc.pending && Dana.approvalSession(item) === svc.sessionId)
  })
  readonly property int rowCount: (convoRow ? 1 : 0) + items.length
  property int cursor: 0
  readonly property int itemIndex: cursor - (convoRow ? 1 : 0)
  readonly property var selected: itemIndex >= 0 && itemIndex < items.length ? items[itemIndex] : null

  onRowCountChanged: cursor = Math.max(0, Math.min(cursor, rowCount - 1))
  onOpenedChanged: if (opened) { cursor = 0; if (svc) svc.refreshNow() }

  readonly property color fg: Commons.Color.popups.text
  readonly property color accent: Commons.Color.accent
  readonly property int smallRadius: Math.min(4, Style.cornerRadius)
  readonly property bool vertical: !!bar && bar.vertical === true

  // ---------- actions ----------
  function ask(payload) {
    root.close()
    if (root.bar && root.bar.shell && typeof root.bar.shell.summon === "function")
      root.bar.shell.summon("dana", JSON.stringify(payload || {}))
  }

  function activate(item, action) {
    if (!item || !action) return
    if (!action.client) { root.svc.act(item, action); return }
    // Client actions: an approval opens its conversation here; anything else
    // (a phone notification to reply to, a card's details) opens on the web.
    var session = Dana.approvalSession(item)
    if (session) root.ask({ session: session })
    else { root.close(); Qt.openUrlExternally(root.svc.url + "/") }
  }

  function activateCursor() {
    if (root.convoRow && root.cursor === 0) { root.ask({}); return }
    if (root.selected) activate(root.selected, root.selected.primary)
  }

  function secondary(verb) {
    var a = Dana.secondaryOf(root.selected, verb)
    if (a) root.svc.act(root.selected, a)
  }

  function lede() {
    if (root.svc && root.svc.now && root.svc.now.lede) return root.svc.now.lede
    return new Date().toLocaleDateString(Qt.locale(Commons.I18n.formatLocale), "dddd d MMMM")
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: String.fromCodePoint(0xF0AE2)
    active: !!root.svc && root.svc.pending !== null
    opacity: !root.ready ? 0.45 : 1
    labelText: root.ready && root.items.length > 0 && !vertical ? Commons.I18n.formatNumber(root.items.length, 0) : ""
    slotSize: Style.bar.iconSlot * (labelText !== "" ? 1.7 : 1)
    tooltipText: ""
    onPressed: function(b) {
      if (b === Qt.RightButton) root.ask({})
      else if (b === Qt.MiddleButton) { if (root.svc) root.svc.refreshNow() }
      else root.toggle()
    }

    // A vertical bar has no room for the count: a dot says the same.
    Rectangle {
      visible: root.vertical && root.ready && root.items.length > 0
      width: Style.space(6)
      height: width
      radius: width / 2
      color: root.accent
      x: parent.width / 2 + Style.space(5)
      y: parent.height / 2 - Style.space(10)
    }

    SequentialAnimation on opacity {
      running: !!root.svc && root.svc.working && Style.duration(900) > 0
      loops: Animation.Infinite
      alwaysRunToEnd: true
      NumberAnimation { to: 0.4; duration: Style.duration(900); easing.type: Easing.InOutSine }
      NumberAnimation { to: 1; duration: Style.duration(900); easing.type: Easing.InOutSine }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: panel.loadedContent
    readonly property real horizontalInset: padding * 2 + Border.left(borderSpec) + Border.right(borderSpec)
    contentWidth: panel.fittedContentWidth(horizontalInset + Style.space(420))
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight)

    content: PanelKeyCatcher {
      id: keys
      anchors.fill: parent
      implicitHeight: column.implicitHeight
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onMoveRequested: function(dx, dy) {
        if (dy !== 0 && root.rowCount > 0) root.cursor = Math.max(0, Math.min(root.rowCount - 1, root.cursor + dy))
      }
      onActivateRequested: root.activateCursor()
      onDeleteRequested: root.secondary("dismiss")
      onTextKey: function(t) {
        if (t === "s") root.secondary("snooze")
        else if (t === "a") root.ask({})
        else if (t === "u") root.svc.undo()
        else if (t === "r") root.svc.refreshNow()
        else if (t === "o") { root.close(); Qt.openUrlExternally(root.svc.url + "/") }
      }

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(12)

        // ---------- header ----------
        Column {
          width: parent.width
          spacing: Style.space(2)
          PlainText {
            text: "Dana"
            font.pixelSize: Style.font.title
            font.bold: true
          }
          PlainText {
            width: parent.width
            text: root.ready ? root.lede() : ""
            visible: text !== ""
            wrapMode: Text.Wrap
            opacity: 0.7
          }
        }

        // ---------- setup ----------
        PlainText {
          visible: !!root.svc && !root.ready
          width: parent.width
          wrapMode: Text.Wrap
          opacity: 0.8
          text: !root.svc ? "" : root.svc.setupProblem === "url" ? strings.t("setup.badUrl").arg(root.svc.envPath)
            : root.svc.setupProblem === "token" ? strings.t("setup.badToken").arg(root.svc.envPath)
            : strings.t("setup.body").arg(root.svc.envPath)
        }

        // ---------- the conversation, when it needs a look ----------
        Row {
          id: convo
          visible: root.convoRow
          width: parent.width
          Button {
            width: parent.width
            leftAlign: true
            bordered: true
            hasCursor: root.cursor === 0
            selected: !!root.svc && root.svc.pending !== null
            iconText: String.fromCodePoint(0xF0AE2)
            text: !root.svc ? "" : root.svc.pending
              ? strings.t("panel.waiting").arg(root.svc.pending.title || strings.t("panel.waitingFallback"))
              : strings.t("panel.working").arg(Dana.oneLine(root.svc.lastPrompt, 60) || "…")
            onClicked: root.ask({})
          }
        }

        // ---------- Now ----------
        PlainText {
          visible: root.ready && root.items.length > 0
          text: strings.tn("panel.needsYou", root.items.length).arg(Commons.I18n.formatNumber(root.items.length, 0)).toUpperCase()
          font.pixelSize: Style.font.caption
          font.bold: true
          font.letterSpacing: 1.2
          opacity: 0.6
        }

        Column {
          width: parent.width
          spacing: Style.space(4)
          visible: root.ready

          Repeater {
            model: root.items
            delegate: Rectangle {
              id: row
              required property var modelData
              required property int index
              readonly property bool current: root.itemIndex === index
              width: column.width
              height: rowColumn.implicitHeight + Style.space(16)
              radius: root.smallRadius
              // Hover only tints the card: moving the pointer never changes a
              // card's height, so nothing shifts under it. The keyboard cursor
              // or a click picks the card whose actions show.
              color: current || rowMouse.containsMouse ? Style.hoverFill : "transparent"
              border.width: current ? Style.hoverBorderWidth : 0
              border.color: Style.hoverBorderColor

              MouseArea {
                id: rowMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: row.current ? Qt.ArrowCursor : Qt.PointingHandCursor
                onClicked: root.cursor = row.index + (root.convoRow ? 1 : 0)
              }

              Column {
                id: rowColumn
                x: Style.space(10)
                y: Style.space(8)
                width: parent.width - Style.space(20)
                spacing: Style.space(4)

                PlainText {
                  width: parent.width
                  text: row.modelData.title
                  wrapMode: Text.Wrap
                  maximumLineCount: 2
                  elide: Text.ElideRight
                  font.bold: true
                }
                PlainText {
                  width: parent.width
                  visible: text !== ""
                  text: [row.modelData.source, row.modelData.why].filter(function(s) { return s !== "" }).join(" · ")
                  elide: Text.ElideRight
                  font.pixelSize: Style.font.bodySmall
                  opacity: 0.65
                }

                // Actions show on the current card only, so the list stays calm.
                Flow {
                  visible: row.current
                  width: parent.width
                  spacing: Style.space(6)
                  topPadding: Style.space(4)

                  Button {
                    visible: !!row.modelData.primary
                    bordered: true
                    selected: true
                    text: row.modelData.primary ? row.modelData.primary.label + "  ↵" : ""
                    onClicked: root.activate(row.modelData, row.modelData.primary)
                  }
                  Repeater {
                    model: row.modelData.secondary.filter(function(a) { return !a.client })
                    delegate: Button {
                      bordered: true
                      text: modelData.label + (modelData.action === "dismiss" ? "  x" : modelData.action === "snooze" && Dana.secondaryOf(row.modelData, "snooze") === modelData ? "  s" : "")
                      onClicked: root.svc.act(row.modelData, modelData)
                    }
                  }
                }
              }
            }
          }
        }

        PlainText {
          visible: root.ready && root.items.length === 0
          width: parent.width
          wrapMode: Text.Wrap
          opacity: 0.6
          text: !root.svc ? "" : root.svc.nowErrorKind ? strings.t("error." + root.svc.nowErrorKind)
            : root.svc.now ? strings.t("panel.empty") : strings.t("panel.loading")
        }

        // ---------- what just happened ----------
        Item {
          visible: !!root.svc && root.svc.toastText !== ""
          width: parent.width
          height: Math.max(toastLabel.implicitHeight, undoButton.implicitHeight)

          PlainText {
            id: toastLabel
            anchors.left: parent.left
            anchors.right: undoButton.left
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            elide: Text.ElideRight
            color: root.svc && root.svc.toastError ? Commons.Color.urgent : root.fg
            text: root.svc ? root.svc.toastText : ""
          }
          Button {
            id: undoButton
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            visible: !!root.svc && root.svc.toastUndo !== ""
            bordered: true
            text: strings.t("panel.undo") + "  u"
            onClicked: root.svc.undo()
          }
        }

        PanelSeparator { width: parent.width; visible: root.ready }

        // ---------- footer ----------
        Item {
          visible: root.ready
          width: parent.width
          height: askButton.implicitHeight

          Button {
            id: askButton
            anchors.left: parent.left
            bordered: true
            iconText: String.fromCodePoint(0xF0AE2)
            text: strings.t("panel.ask") + "  a"
            onClicked: root.ask({})
          }
          PlainText {
            anchors.right: parent.right
            anchors.left: askButton.right
            anchors.leftMargin: Style.space(10)
            anchors.verticalCenter: parent.verticalCenter
            horizontalAlignment: Text.AlignRight
            elide: Text.ElideLeft
            font.pixelSize: Style.font.bodySmall
            opacity: 0.6
            visible: !!root.svc && !!root.svc.now && root.svc.now.notifications.total > 0
            text: !root.svc || !root.svc.now ? ""
              : strings.tn("panel.notifications", root.svc.now.notifications.total).arg(Commons.I18n.formatNumber(root.svc.now.notifications.total, 0))
          }
        }
      }
    }
  }

  component PlainText: Text {
    textFormat: Text.PlainText
    color: root.fg
    font.family: Style.font.family
    font.pixelSize: Style.font.body
  }
}
