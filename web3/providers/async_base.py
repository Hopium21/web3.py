import itertools
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Coroutine,
    Set,
    Tuple,
    cast,
)

from eth_utils import (
    is_text,
    to_bytes,
    to_text,
)

from web3._utils.encoding import (
    FriendlyJsonSerde,
    Web3JsonEncoder,
)
from web3.exceptions import (
    ProviderConnectionError,
)
from web3.middleware import (
    async_combine_middlewares,
)
from web3.middleware.base import (
    Web3Middleware,
)
from web3.types import (
    AsyncMiddlewareOnion,
    MiddlewareOnion,
    RPCEndpoint,
    RPCResponse,
)
from web3.utils import (
    SimpleCache,
)

if TYPE_CHECKING:
    from web3 import (  # noqa: F401
        AsyncWeb3,
        WebsocketProviderV2,
    )


CACHEABLE_REQUESTS = cast(
    Set[RPCEndpoint],
    (
        "eth_chainId",
        "eth_getBlockByHash",
        "eth_getBlockTransactionCountByHash",
        "eth_getRawTransactionByHash",
        "eth_getTransactionByBlockHashAndIndex",
        "eth_getTransactionByHash",
        "eth_getUncleByBlockHashAndIndex",
        "eth_getUncleCountByBlockHash",
        "net_version",
        "web3_clientVersion",
    ),
)


class AsyncBaseProvider:
    _middlewares: Tuple[Web3Middleware, ...] = ()
    _request_func_cache: Tuple[
        Tuple[Web3Middleware, ...], Callable[..., Coroutine[Any, Any, RPCResponse]]
    ] = (None, None)

    is_async = True
    has_persistent_connection = False
    global_ccip_read_enabled: bool = True
    ccip_read_max_redirects: int = 4

    # request caching
    cache_allowed_requests: bool = False
    cacheable_requests: Set[RPCEndpoint] = CACHEABLE_REQUESTS
    _request_cache: SimpleCache

    def __init__(self) -> None:
        self._request_cache = SimpleCache(1000)

    @property
    def middlewares(self) -> Tuple[Web3Middleware, ...]:
        return self._middlewares

    @middlewares.setter
    def middlewares(self, values: MiddlewareOnion) -> None:
        # tuple(values) converts to MiddlewareOnion -> Tuple[Middleware, ...]
        self._middlewares = tuple(values)  # type: ignore

    async def request_func(
        self, async_w3: "AsyncWeb3", outer_middlewares: AsyncMiddlewareOnion
    ) -> Callable[..., Coroutine[Any, Any, RPCResponse]]:
        # type ignored b/c tuple(MiddlewareOnion) converts to tuple of middlewares
        all_middlewares: Tuple[Web3Middleware] = tuple(outer_middlewares) + tuple(self.middlewares)  # type: ignore  # noqa: E501

        cache_key = self._request_func_cache[0]
        if cache_key != all_middlewares:
            self._request_func_cache = (
                all_middlewares,
                await async_combine_middlewares(
                    middlewares=all_middlewares,
                    async_w3=async_w3,
                    provider_request_fn=self.make_request,
                ),
            )
        return self._request_func_cache[-1]

    async def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        raise NotImplementedError("Providers must implement this method")

    async def is_connected(self, show_traceback: bool = False) -> bool:
        raise NotImplementedError("Providers must implement this method")


class AsyncJSONBaseProvider(AsyncBaseProvider):
    def __init__(self) -> None:
        super().__init__()
        self.request_counter = itertools.count()

    def encode_rpc_request(self, method: RPCEndpoint, params: Any) -> bytes:
        request_id = next(self.request_counter)
        rpc_dict = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or [],
            "id": request_id,
        }
        encoded = FriendlyJsonSerde().json_encode(rpc_dict, cls=Web3JsonEncoder)
        return to_bytes(text=encoded)

    def decode_rpc_response(self, raw_response: bytes) -> RPCResponse:
        text_response = str(
            to_text(raw_response) if not is_text(raw_response) else raw_response
        )
        return cast(RPCResponse, FriendlyJsonSerde().json_decode(text_response))

    async def is_connected(self, show_traceback: bool = False) -> bool:
        try:
            response = await self.make_request(RPCEndpoint("web3_clientVersion"), [])
        except OSError as e:
            if show_traceback:
                raise ProviderConnectionError(
                    f"Problem connecting to provider with error: {type(e)}: {e}"
                )
            return False

        if "error" in response:
            if show_traceback:
                raise ProviderConnectionError(
                    f"Error received from provider: {response}"
                )
            return False

        if response["jsonrpc"] == "2.0":
            return True
        else:
            if show_traceback:
                raise ProviderConnectionError(f"Bad jsonrpc version: {response}")
            return False
