package io.github.ronganam.dana.data.network

/**
 * The Dana server's REST/SSE API, one typed client per area. Built per
 * request site from [ServerConfig.api] (a changed server or token applies to
 * the next call); every area shares one [ApiClient] transport.
 */
class ApiService(serverUrl: String, apiToken: String) {
    val http = ApiClient(serverUrl, apiToken)

    val chat = ChatApi(http)
    val tasks = TasksApi(http)
    val calls = CallsApi(http)
    val notifications = NotificationsApi(http)
    val now = NowApi(http)
    val people = PeopleApi(http)
    val about = AboutApi(http)
    val devices = DevicesApi(http)
    val pairing = PairingApi(http)
    val money = io.github.ronganam.dana.data.money.MoneyDeviceApi(http)
    val shopping = io.github.ronganam.dana.data.shopping.ShoppingApi(http)
}
