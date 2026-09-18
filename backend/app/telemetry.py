"""Opt-in console spans; no opaque external telemetry destination."""
import os,json,logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor,ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

def configure(app):
    if os.environ.get('OTEL_CONSOLE_ENABLED','0')!='1':return
    provider=TracerProvider(resource=Resource.create({'service.name':'visionops-api','service.version':'0.1.0'}));provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()));trace.set_tracer_provider(provider);FastAPIInstrumentor.instrument_app(app)
