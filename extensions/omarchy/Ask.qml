import QtQuick
import Quickshell
import Quickshell.Wayland
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "lib/Dana.js" as Dana

// Quick ask: a card in the upper middle of the screen, over a light scrim.
// Type, press Enter, and the answer streams in under the question. Escape
// closes it and Dana keeps going: the answer (or a question back) arrives as
// a notification, and opening this again shows it.
//
// It writes to the shared conversation (`main`), so a question asked here
// continues on the phone and the other way round, and after twenty quiet
// minutes the next question starts fresh (the server decides that). What is
// shown is what Dana remembers now; older messages stay on the phone and web.
//
// Summon payload (all optional): {"capture": "region"} picks a part of the
// screen first, {"session": "<id>"} opens another conversation (an approval
// waiting there), {"text": "…"} prefills the question.
Item {
  id: root

  property var shell: null
  property var manifest: null
  property var service: null
  property string omarchyPath: ""

  property bool opened: false

  function resolveService() {
    if (!root.service && root.shell && typeof root.shell.serviceFor === "function")
      root.service = root.shell.serviceFor("dana")
  }

  function open(payloadJson) {
    var payload = ({})
    try { payload = JSON.parse(payloadJson || "{}") || ({}) } catch (e) { payload = ({}) }
    resolveService()
    if (!root.service) return
    if (payload.session) root.service.openSession(String(payload.session))
    if (typeof payload.text === "string") card.setText(Dana.cleanText(payload.text, 4000))
    if (payload.capture === "region") {
      root.capture(true)
      return
    }
    root.show()
  }

  function show() {
    root.opened = true
    root.service.overlayOpen = true
    root.service.ensureFresh()
    Qt.callLater(card.focusInput)
  }

  // The card hides while the region is picked, so it is not in the shot.
  // Cancelled from the hotkey: nothing opens. From the card: it comes back.
  // A failure always opens the card, which says what went wrong.
  function capture(fromHotkey) {
    root.opened = false
    root.service.overlayOpen = true
    root.service.captureRegion(function(ok) {
      if (ok || !fromHotkey || root.service.errorKind !== "") root.show()
      else root.dismiss()
    })
  }

  function close() {
    root.opened = false
    if (root.service) root.service.overlayOpen = false
  }

  function dismiss() {
    root.close()
    if (root.shell && typeof root.shell.hide === "function") root.shell.hide((root.manifest && root.manifest.id) || "dana")
  }

  function toggle() {
    if (root.opened) root.dismiss()
    else root.open("{}")
  }

  OverlayWindow {
    id: window
    shown: root.opened
    WlrLayershell.namespace: "dana-ask"

    Rectangle {
      anchors.fill: parent
      color: Commons.Color.menu.scrim
    }

    MouseArea {
      anchors.fill: parent
      onClicked: root.dismiss()
    }

    AskCard {
      id: card
      svc: root.service
      width: Math.min(Style.space(700), parent.width - Style.gapsOut * 8)
      x: Math.round((parent.width - width) / 2)
      y: Math.round(parent.height * 0.16)
      height: Math.min(implicitHeight, parent.height - y - Style.gapsOut * 4)
      conversationMax: parent.height * 0.58
      onDismissRequested: root.dismiss()
      onCaptureRequested: root.capture(false)
    }
  }
}
