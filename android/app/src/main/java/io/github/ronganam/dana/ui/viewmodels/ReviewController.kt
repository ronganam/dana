package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.data.models.Briefing
import io.github.ronganam.dana.data.models.ReviewItem
import io.github.ronganam.dana.data.models.WeeklyReview
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** Weekly review (Saturday evening) and today's wake briefing. */
class ReviewController(private val ctx: ControllerContext) {

    private val _review = MutableStateFlow<WeeklyReview?>(null)
    val review: StateFlow<WeeklyReview?> = _review.asStateFlow()

    /** The items as the user edits them (action / date / group / enabled). */
    private val _items = MutableStateFlow<List<ReviewItem>>(emptyList())
    val items: StateFlow<List<ReviewItem>> = _items.asStateFlow()

    private val _loading = MutableStateFlow(false)
    val loading: StateFlow<Boolean> = _loading.asStateFlow()

    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()

    private val _briefing = MutableStateFlow<Briefing?>(null)
    val briefing: StateFlow<Briefing?> = _briefing.asStateFlow()

    fun fetch() {
        ctx.scope.launch {
            _loading.value = true
            try {
                ctx.load("Review") { ctx.api.getLatestReview() }.let { setReview(it) }
            } finally {
                _loading.value = false
            }
        }
    }

    fun fetchBriefing() {
        ctx.scope.launch {
            ctx.load("Briefing") { ctx.api.getBriefingToday() }?.let { _briefing.value = it }
        }
    }

    private fun setReview(r: WeeklyReview?) {
        _review.value = r
        // A "new date" proposal for an overdue item often still carries the old (past) date: start it at tomorrow instead.
        val tomorrow = java.time.LocalDate.now().plusDays(1).toString()
        _items.value = r?.items.orEmpty().map {
            if ((it.action == "reschedule" || it.action == "reach_out") && (it.dueDate.isNullOrBlank() || it.dueDate.take(10) < tomorrow))
                it.copy(dueDate = tomorrow) else it
        }
    }

    fun edit(ref: String, change: (ReviewItem) -> ReviewItem) {
        _items.value = _items.value.map { if (it.ref == ref) change(it) else it }
    }

    fun generate() {
        ctx.scope.launch {
            _busy.value = true
            try {
                val r = ctx.load("ReviewGenerate") { ctx.api.generateReview() }
                if (r == null) ctx.messages.post("Couldn't prepare the review") else setReview(r)
            } finally {
                _busy.value = false
            }
        }
    }

    fun apply(onDone: () -> Unit = {}) {
        val r = _review.value ?: return
        ctx.scope.launch {
            _busy.value = true
            try {
                val res = ctx.load("ReviewApply") { ctx.api.applyReview(r.id, _items.value) }
                if (res == null || res.has("error")) {
                    ctx.messages.post("Couldn't apply the review" + (res?.optString("error")?.let { ": $it" } ?: ""))
                } else {
                    val applied = res.optInt("applied"); val failed = res.optInt("failed")
                    ctx.messages.post(if (failed > 0) "Done: $applied changes, $failed failed" else "Done: $applied changes")
                    fetch()
                    onDone()
                }
            } finally {
                _busy.value = false
            }
        }
    }

    fun dismiss() {
        val r = _review.value ?: return
        ctx.action("Couldn't skip the review") {
            ctx.api.dismissReview(r.id).also { if (it) fetch() }
        }
    }
}
