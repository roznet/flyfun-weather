#if DEBUG
import Foundation
import FlyFunCommon

extension CachingBriefingRepository {
    /// Build a repository around an injected `online` double and a temp-dir
    /// cache, for unit tests of the offline/cache-first read paths (#359).
    ///
    /// The `client` is an inert stub `APIClient` (dummy base URL, in-memory
    /// token store, never authenticated). The flight-list read path exercised by
    /// these tests goes through `online` and `cache` only and never touches the
    /// client, so it stays a placeholder. Lives in the app module (guarded by
    /// `DEBUG`) because the test target doesn't link `FlyFunCommon`.
    static func makeForTesting(
        online: any BriefingRepository,
        cache: BriefingCacheStore
    ) -> CachingBriefingRepository {
        let store = InMemoryBearerTokenStore()
        let session = RollingBearerSession(store: store)
        let client = APIClient(
            baseURL: URL(string: "https://tests.invalid")!,
            tokenStore: store,
            rollingSession: session
        )
        return CachingBriefingRepository(client: client, online: online, cache: cache)
    }
}
#endif
