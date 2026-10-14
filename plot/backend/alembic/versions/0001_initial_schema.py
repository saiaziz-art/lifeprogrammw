"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-11 22:18:31.901615
"""
from alembic import op
import sqlalchemy as sa
import geoalchemy2
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table('api_cache',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('status', sa.Integer(), nullable=False),
    sa.Column('body', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor', sa.String(length=320), nullable=False),
    sa.Column('action', sa.String(length=120), nullable=False),
    sa.Column('entity', sa.String(length=60), nullable=True),
    sa.Column('entity_id', sa.String(length=60), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_log_at'), 'audit_log', ['at'], unique=False)
    op.create_table('cost_rates',
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('category', sa.String(length=60), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('unit', sa.String(length=30), nullable=False),
    sa.Column('low', sa.Float(), nullable=False),
    sa.Column('base', sa.Float(), nullable=False),
    sa.Column('high', sa.Float(), nullable=False),
    sa.Column('basis', sa.Text(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('source_date', sa.Date(), nullable=False),
    sa.Column('regional', sa.Boolean(), nullable=False),
    sa.Column('user_override', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('preferences',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('preferences_history',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('saved_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('saved_by', sa.String(length=320), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('schedules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('cron', sa.String(length=60), nullable=False),
    sa.Column('timezone', sa.String(length=60), nullable=False),
    sa.Column('job_type', sa.String(length=60), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('last_enqueued_for', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('secrets',
    sa.Column('key', sa.String(length=120), nullable=False),
    sa.Column('ciphertext', sa.LargeBinary(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_by', sa.String(length=320), nullable=True),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_geospatial_table('sites',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('point', Geometry(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry', nullable=False), nullable=False),
    sa.Column('boundary', Geometry(srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'), nullable=True),
    sa.Column('boundary_source', sa.String(length=30), nullable=False),
    sa.Column('area_m2', sa.Float(), nullable=True),
    sa.Column('area_source', sa.String(length=60), nullable=True),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('postcode', sa.String(length=10), nullable=True),
    sa.Column('lpa_code', sa.String(length=12), nullable=True),
    sa.Column('lpa_name', sa.String(length=120), nullable=True),
    sa.Column('county', sa.String(length=120), nullable=True),
    sa.Column('jurisdiction', sa.String(length=20), nullable=False),
    sa.Column('uprn', sa.String(length=20), nullable=True),
    sa.Column('title_numbers', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('inspire_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('origin', sa.String(length=20), nullable=False),
    sa.Column('category', sa.String(length=30), nullable=False),
    sa.Column('headline', sa.String(length=300), nullable=True),
    sa.Column('dedupe_key', sa.String(length=120), nullable=True),
    sa.Column('provenance', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('confidence', sa.String(length=10), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_changed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('enriched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('excluded', sa.Boolean(), nullable=False),
    sa.Column('exclusion_reason', sa.Text(), nullable=True),
    sa.Column('stale', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_geospatial_index('idx_sites_boundary', 'sites', ['boundary'], unique=False, postgresql_using='gist', postgresql_ops={})
    op.create_geospatial_index('idx_sites_point', 'sites', ['point'], unique=False, postgresql_using='gist', postgresql_ops={})
    op.create_index(op.f('ix_sites_dedupe_key'), 'sites', ['dedupe_key'], unique=False)
    op.create_table('sources',
    sa.Column('id', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('category', sa.String(length=40), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('licence', sa.String(length=200), nullable=True),
    sa.Column('attribution', sa.Text(), nullable=True),
    sa.Column('terms_url', sa.String(length=500), nullable=True),
    sa.Column('docs_url', sa.String(length=500), nullable=True),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_data_change_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_status', sa.String(length=30), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('consecutive_failures', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('suppressions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('match_hash', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=300), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('match_hash')
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('is_admin', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('evidence',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('site_id', sa.Integer(), nullable=False),
    sa.Column('topic', sa.String(length=30), nullable=False),
    sa.Column('kind', sa.String(length=60), nullable=False),
    sa.Column('source_id', sa.String(length=80), nullable=True),
    sa.Column('source_url', sa.String(length=1000), nullable=True),
    sa.Column('source_ref', sa.String(length=300), nullable=True),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('data_date', sa.Date(), nullable=True),
    sa.Column('retrieved_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('licence', sa.String(length=200), nullable=True),
    sa.Column('attribution', sa.Text(), nullable=True),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('superseded_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'fingerprint', name='uq_evidence_fingerprint')
    )
    op.create_index(op.f('ix_evidence_site_id'), 'evidence', ['site_id'], unique=False)
    op.create_table('expenses',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('category', sa.String(length=60), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('amount_gbp', sa.Float(), nullable=False),
    sa.Column('incurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('site_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('feedback',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('site_id', sa.Integer(), nullable=False),
    sa.Column('signal', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(length=60), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_feedback_site_id'), 'feedback', ['site_id'], unique=False)
    op.create_table('jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(length=60), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('run_after', sa.DateTime(timezone=True), nullable=False),
    sa.Column('locked_by', sa.String(length=120), nullable=True),
    sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('schedule_id', sa.Integer(), nullable=True),
    sa.Column('scheduled_for', sa.DateTime(timezone=True), nullable=True),
    sa.Column('dedupe_key', sa.String(length=200), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['schedule_id'], ['schedules.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dedupe_key')
    )
    op.create_index('ix_jobs_claim', 'jobs', ['status', 'run_after'], unique=False)
    op.create_table('leads',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('site_id', sa.Integer(), nullable=False),
    sa.Column('stage', sa.String(length=40), nullable=False),
    sa.Column('signals', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('owner', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('availability_evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id')
    )
    op.create_table('listings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('site_id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.String(length=80), nullable=False),
    sa.Column('external_ref', sa.String(length=300), nullable=False),
    sa.Column('url', sa.String(length=1000), nullable=True),
    sa.Column('title', sa.String(length=500), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('asking_price', sa.Integer(), nullable=True),
    sa.Column('price_qualifier', sa.String(length=60), nullable=True),
    sa.Column('tenure', sa.String(length=40), nullable=True),
    sa.Column('reported_area_m2', sa.Float(), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('agent', sa.String(length=300), nullable=True),
    sa.Column('is_auction', sa.Boolean(), nullable=False),
    sa.Column('auction_date', sa.Date(), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_changed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('provenance', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'external_ref', name='uq_listing_source_ref')
    )
    op.create_index(op.f('ix_listings_site_id'), 'listings', ['site_id'], unique=False)
    op.create_table('overrides',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('site_id', sa.Integer(), nullable=False),
    sa.Column('field', sa.String(length=60), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(length=320), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_overrides_site_id'), 'overrides', ['site_id'], unique=False)
    op.create_table('pipeline_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('site_id', sa.Integer(), nullable=False),
    sa.Column('stage', sa.String(length=30), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('checklist', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id')
    )
    op.create_table('user_sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('user_agent', sa.String(length=400), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_user_sessions_user_id'), 'user_sessions', ['user_id'], unique=False)
    op.create_table('lead_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('lead_id', sa.Integer(), nullable=False),
    sa.Column('from_stage', sa.String(length=40), nullable=True),
    sa.Column('to_stage', sa.String(length=40), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor', sa.String(length=320), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_lead_events_lead_id'), 'lead_events', ['lead_id'], unique=False)
    op.create_table('outreach',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('lead_id', sa.Integer(), nullable=False),
    sa.Column('channel', sa.String(length=10), nullable=False),
    sa.Column('recipient_type', sa.String(length=20), nullable=False),
    sa.Column('recipient_name', sa.String(length=300), nullable=True),
    sa.Column('recipient_address', sa.Text(), nullable=True),
    sa.Column('recipient_email', sa.String(length=320), nullable=True),
    sa.Column('subject', sa.String(length=300), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('compliance', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('approved_by', sa.String(length=320), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_outreach_lead_id'), 'outreach', ['lead_id'], unique=False)
    op.create_table('runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('trigger', sa.String(length=20), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('steps', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('summary', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('assessments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('site_id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('is_current', sa.Boolean(), nullable=False),
    sa.Column('inputs_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('preferences_version', sa.Integer(), nullable=False),
    sa.Column('engine_version', sa.String(length=20), nullable=False),
    sa.Column('planning', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('site_risk', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('design', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('costs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('residual', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('score', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('total_score', sa.Float(), nullable=False),
    sa.Column('fatal', sa.Boolean(), nullable=False),
    sa.Column('data_confidence', sa.Float(), nullable=False),
    sa.Column('explanation', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_assessment_current', 'assessments', ['site_id', 'is_current'], unique=False)
    op.create_index(op.f('ix_assessments_site_id'), 'assessments', ['site_id'], unique=False)
    op.create_table('digests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('digest_date', sa.Date(), nullable=False),
    sa.Column('items', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('body_text', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('delivery_status', sa.String(length=20), nullable=False),
    sa.Column('delivery_error', sa.Text(), nullable=True),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_digests_digest_date'), 'digests', ['digest_date'], unique=False)
    op.create_table('listing_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('listing_id', sa.Integer(), nullable=False),
    sa.Column('event', sa.String(length=30), nullable=False),
    sa.Column('old', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('new', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_listing_events_listing_id'), 'listing_events', ['listing_id'], unique=False)
    op.create_table('source_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.String(length=80), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('records_fetched', sa.Integer(), nullable=False),
    sa.Column('records_new', sa.Integer(), nullable=False),
    sa.Column('records_changed', sa.Integer(), nullable=False),
    sa.Column('http_status', sa.Integer(), nullable=True),
    sa.Column('error_class', sa.String(length=60), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('data_date', sa.Date(), nullable=True),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_source_runs_run_id'), 'source_runs', ['run_id'], unique=False)
    op.create_index(op.f('ix_source_runs_source_id'), 'source_runs', ['source_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_source_runs_source_id'), table_name='source_runs')
    op.drop_index(op.f('ix_source_runs_run_id'), table_name='source_runs')
    op.drop_table('source_runs')
    op.drop_index(op.f('ix_listing_events_listing_id'), table_name='listing_events')
    op.drop_table('listing_events')
    op.drop_index(op.f('ix_digests_digest_date'), table_name='digests')
    op.drop_table('digests')
    op.drop_index(op.f('ix_assessments_site_id'), table_name='assessments')
    op.drop_index('ix_assessment_current', table_name='assessments')
    op.drop_table('assessments')
    op.drop_table('runs')
    op.drop_index(op.f('ix_outreach_lead_id'), table_name='outreach')
    op.drop_table('outreach')
    op.drop_index(op.f('ix_lead_events_lead_id'), table_name='lead_events')
    op.drop_table('lead_events')
    op.drop_index(op.f('ix_user_sessions_user_id'), table_name='user_sessions')
    op.drop_table('user_sessions')
    op.drop_table('pipeline_items')
    op.drop_index(op.f('ix_overrides_site_id'), table_name='overrides')
    op.drop_table('overrides')
    op.drop_index(op.f('ix_listings_site_id'), table_name='listings')
    op.drop_table('listings')
    op.drop_table('leads')
    op.drop_index('ix_jobs_claim', table_name='jobs')
    op.drop_table('jobs')
    op.drop_index(op.f('ix_feedback_site_id'), table_name='feedback')
    op.drop_table('feedback')
    op.drop_table('expenses')
    op.drop_index(op.f('ix_evidence_site_id'), table_name='evidence')
    op.drop_table('evidence')
    op.drop_table('users')
    op.drop_table('suppressions')
    op.drop_table('sources')
    op.drop_index(op.f('ix_sites_dedupe_key'), table_name='sites')
    op.drop_geospatial_index('idx_sites_point', table_name='sites', postgresql_using='gist', column_name='point')
    op.drop_geospatial_index('idx_sites_boundary', table_name='sites', postgresql_using='gist', column_name='boundary')
    op.drop_geospatial_table('sites')
    op.drop_table('secrets')
    op.drop_table('schedules')
    op.drop_table('preferences_history')
    op.drop_table('preferences')
    op.drop_table('cost_rates')
    op.drop_index(op.f('ix_audit_log_at'), table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_table('api_cache')
