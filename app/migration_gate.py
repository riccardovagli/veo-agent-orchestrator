"""Temporary migration admission barrier shared by backend and MCP.

Every admitted HTTP request owns a durable ticket until the ASGI application
returns, including Starlette BackgroundTasks. Tickets never expire implicitly.
No cancellation of admitted requests or resubmission of jobs.
"""
import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from google.cloud import firestore
from starlette.responses import JSONResponse

CONTROL = '_framebutcher_migration/control'

class MigrationGate:
    def __init__(self, app, db_factory, service):
        self.app = app
        self.db_factory = db_factory
        self.service = service

    def enter(self):
        db = self.db_factory()
        control = db.document(CONTROL)
        ticket = control.collection('requests').document(str(uuid.uuid4()))
        @firestore.transactional
        def admit(tx):
            state = control.get(transaction=tx)
            data = state.to_dict() or {}
            if data.get('frozen') is True:
                return False
            tx.create(ticket, {
                'service': self.service,
                'revision': os.environ.get('K_REVISION', 'local'),
                'started_at': datetime.now(timezone.utc),
            })
            return True
        return ticket if admit(db.transaction()) else None

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] == 'OPTIONS':
            return await self.app(scope, receive, send)
        # Health endpoints must remain available while admission is closed.
        if scope['method'] == 'GET' and scope['path'] == '/':
            return await self.app(scope, receive, send)
        try:
            ticket = await asyncio.to_thread(self.enter)
        except Exception:
            logging.exception('Migration admission check failed')
            ticket = None
        if ticket is None:
            return await JSONResponse(
                {'detail': 'Manutenzione in corso. Riprova più tardi.'},
                status_code=503, headers={'Retry-After': '60'},
            )(scope, receive, send)
        try:
            await self.app(scope, receive, send)
        finally:
            # A failure leaves a visible ticket: fail closed, never assume idle.
            try:
                await asyncio.shield(asyncio.to_thread(ticket.delete))
            except BaseException:
                logging.exception('Migration ticket cleanup failed; review required')
                raise


def migration_tool(db_factory):
    """Track the whole MCP tool invocation, independently of its transport."""
    import functools
    from fastapi import HTTPException
    def decorate(function):
        @functools.wraps(function)
        async def guarded(*args, **kwargs):
            ticket = await asyncio.to_thread(MigrationGate(None, db_factory, 'mcp-tool').enter)
            if ticket is None:
                raise HTTPException(503, 'Manutenzione in corso. Riprova più tardi.')
            try:
                return await function(*args, **kwargs)
            finally:
                await asyncio.shield(asyncio.to_thread(ticket.delete))
        return guarded
    return decorate
