"""Entry point: uvicorn run."""

import uvicorn

if __name__ == '__main__':
    uvicorn.run(
        'trading_bot.dashboard_v2.__init__:app',
        host='0.0.0.0',
        port=5090,
        reload=True,
    )
