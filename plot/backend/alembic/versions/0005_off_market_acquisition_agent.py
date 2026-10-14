"""off-market acquisition agent

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-11 23:32:43.783949
"""
from alembic import op
import sqlalchemy as sa
import geoalchemy2
from sqlalchemy.dialects import postgresql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('mail_accounts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('email_address', sa.String(length=320), nullable=False),
    sa.Column('token_ciphertext', sa.LargeBinary(), nullable=False),
    sa.Column('scopes', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('connected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('sync_cursor', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('oauth_states',
    sa.Column('state', sa.String(length=80), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('verifier_ciphertext', sa.LargeBinary(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('state')
    )
    op.create_table('title_documents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('lead_id', sa.Integer(), nullable=True),
    sa.Column('site_id', sa.Integer(), nullable=True),
    sa.Column('filename', sa.String(length=300), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('content', sa.LargeBinary(), nullable=False),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('title_number', sa.String(length=20), nullable=True),
    sa.Column('tenure', sa.String(length=20), nullable=True),
    sa.Column('extracted', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('source_note', sa.Text(), nullable=True),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('uploaded_by', sa.String(length=320), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('verified_by', sa.String(length=320), nullable=True),
    sa.Column('retention_until', sa.Date(), nullable=True),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sha256')
    )
    op.create_index(op.f('ix_title_documents_lead_id'), 'title_documents', ['lead_id'], unique=False)
    op.create_table('contacts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('lead_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('salutation', sa.String(length=120), nullable=True),
    sa.Column('address_lines', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('postcode', sa.String(length=10), nullable=True),
    sa.Column('country', sa.String(length=60), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=True),
    sa.Column('email_basis', sa.String(length=30), nullable=True),
    sa.Column('source', sa.String(length=40), nullable=False),
    sa.Column('source_ref', sa.String(length=300), nullable=True),
    sa.Column('source_document_id', sa.Integer(), nullable=True),
    sa.Column('verified', sa.Boolean(), nullable=False),
    sa.Column('verified_by', sa.String(length=320), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('retention_until', sa.Date(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_document_id'], ['title_documents.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_contacts_lead_id'), 'contacts', ['lead_id'], unique=False)
    op.create_table('inbound_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('lead_id', sa.Integer(), nullable=False),
    sa.Column('outreach_id', sa.Integer(), nullable=True),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('from_name', sa.String(length=300), nullable=True),
    sa.Column('from_address', sa.String(length=500), nullable=True),
    sa.Column('subject', sa.String(length=500), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('provider_message_id', sa.String(length=300), nullable=True),
    sa.Column('category', sa.String(length=30), nullable=False),
    sa.Column('category_source', sa.String(length=20), nullable=False),
    sa.Column('category_confidence', sa.Float(), nullable=False),
    sa.Column('evidence', sa.Text(), nullable=True),
    sa.Column('decline_reason', sa.String(length=40), nullable=True),
    sa.Column('handled', sa.Boolean(), nullable=False),
    sa.Column('suggested_reply_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['outreach_id'], ['outreach.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['suggested_reply_id'], ['outreach.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider_message_id')
    )
    op.create_index(op.f('ix_inbound_messages_lead_id'), 'inbound_messages', ['lead_id'], unique=False)
    op.add_column('outreach', sa.Column('footer', sa.Text(), nullable=True))
    op.add_column('digests', sa.Column('briefing', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'))
    op.add_column('leads', sa.Column('opportunity_type', sa.String(length=40), nullable=False, server_default='register_site'))
    op.add_column('leads', sa.Column('ownership_status', sa.String(length=30), nullable=False, server_default='unknown'))
    op.add_column('leads', sa.Column('analysis', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'))
    op.add_column('leads', sa.Column('acq_score', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'))
    op.add_column('leads', sa.Column('acq_score_total', sa.Float(), nullable=True))
    op.add_column('leads', sa.Column('acq_confidence', sa.Float(), nullable=True))
    op.add_column('leads', sa.Column('acq_fatal', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('leads', sa.Column('benefits', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'))
    op.add_column('leads', sa.Column('contact_method', sa.String(length=30), nullable=True))
    op.add_column('leads', sa.Column('next_action', sa.Text(), nullable=True))
    op.add_column('leads', sa.Column('decline_reason', sa.String(length=40), nullable=True))
    op.add_column('leads', sa.Column('closed_reason', sa.Text(), nullable=True))
    op.add_column('leads', sa.Column('stage_changed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('leads', sa.Column('last_contact_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('leads', sa.Column('discovered_run_id', sa.Integer(), nullable=True))
    op.add_column('outreach', sa.Column('contact_id', sa.Integer(), nullable=True))
    op.add_column('outreach', sa.Column('kind', sa.String(length=20), nullable=False, server_default='initial'))
    op.add_column('outreach', sa.Column('parent_id', sa.Integer(), nullable=True))
    op.add_column('outreach', sa.Column('persona', sa.String(length=20), nullable=True))
    op.add_column('outreach', sa.Column('generator', sa.String(length=20), nullable=True))
    op.add_column('outreach', sa.Column('generator_meta', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'))
    op.add_column('outreach', sa.Column('word_count', sa.Integer(), nullable=True))
    op.add_column('outreach', sa.Column('content_hash', sa.String(length=64), nullable=True))
    op.add_column('outreach', sa.Column('recipient_hash', sa.String(length=64), nullable=True))
    op.add_column('outreach', sa.Column('approved_hash', sa.String(length=64), nullable=True))
    op.add_column('outreach', sa.Column('provider', sa.String(length=30), nullable=True))
    op.add_column('outreach', sa.Column('provider_message_id', sa.String(length=300), nullable=True))
    op.add_column('outreach', sa.Column('provider_thread_id', sa.String(length=300), nullable=True))
    op.add_column('outreach', sa.Column('provider_draft_id', sa.String(length=300), nullable=True))
    op.add_column('outreach', sa.Column('delivery_status', sa.String(length=30), nullable=True))
    op.add_column('outreach', sa.Column('delivery_detail', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'))
    op.add_column('outreach', sa.Column('pdf', sa.LargeBinary(), nullable=True))
    op.add_column('outreach', sa.Column('postage_estimate_gbp', sa.Float(), nullable=True))
    op.add_column('outreach', sa.Column('idempotency_key', sa.String(length=64), nullable=True))
    op.add_column('outreach', sa.Column('send_attempted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('outreach', sa.Column('rejected_reason', sa.Text(), nullable=True))
    op.add_column('outreach', sa.Column('review_reason', sa.Text(), nullable=True))
    op.alter_column('outreach', 'status',
               existing_type=sa.VARCHAR(length=20),
               type_=sa.String(length=30),
               existing_nullable=False)
    op.create_index(op.f('ix_outreach_contact_id'), 'outreach', ['contact_id'], unique=False)
    op.create_unique_constraint('uq_outreach_provider_message_id', 'outreach', ['provider_message_id'])
    op.create_unique_constraint('uq_outreach_idempotency_key', 'outreach', ['idempotency_key'])
    op.create_foreign_key('fk_outreach_contact', 'outreach', 'contacts', ['contact_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_outreach_parent', 'outreach', 'outreach', ['parent_id'], ['id'], ondelete='SET NULL')
    op.add_column('suppressions', sa.Column('kind', sa.String(length=20), nullable=False, server_default='name_address'))
    op.add_column('suppressions', sa.Column('source', sa.String(length=40), nullable=True))

    # map the earlier lead stages onto the CRM stages
    op.execute("""
        UPDATE leads SET stage = CASE stage
            WHEN 'potential_site_identified' THEN 'discovered'
            WHEN 'parcel_title_matched' THEN 'feasibility_screened'
            WHEN 'ownership_research_needed' THEN 'feasibility_screened'
            WHEN 'outreach_approved' THEN 'awaiting_approval'
            WHEN 'owner_contacted' THEN 'awaiting_response'
            WHEN 'interest_confirmed' THEN 'interested'
            ELSE stage END,
          ownership_status = CASE WHEN stage IN ('parcel_title_matched','ownership_research_needed') THEN 'title_purchase_needed' ELSE ownership_status END
    """)
    op.execute("UPDATE lead_events SET to_stage = 'discovered' WHERE to_stage = 'potential_site_identified'")


def downgrade() -> None:
    op.drop_column('digests', 'briefing')
    op.drop_column('outreach', 'footer')
    op.drop_column('suppressions', 'source')
    op.drop_column('suppressions', 'kind')
    # WARNING: constraint name is None; this directive will fail as
    # rendered.  Add a name, or use a naming convention; see
    # https://alembic.sqlalchemy.org/en/latest/naming.html
    op.drop_constraint('fk_outreach_parent', 'outreach', type_='foreignkey')
    # WARNING: constraint name is None; this directive will fail as
    # rendered.  Add a name, or use a naming convention; see
    # https://alembic.sqlalchemy.org/en/latest/naming.html
    op.drop_constraint('fk_outreach_contact', 'outreach', type_='foreignkey')
    # WARNING: constraint name is None; this directive will fail as
    # rendered.  Add a name, or use a naming convention; see
    # https://alembic.sqlalchemy.org/en/latest/naming.html
    op.drop_constraint('uq_outreach_idempotency_key', 'outreach', type_='unique')
    # WARNING: constraint name is None; this directive will fail as
    # rendered.  Add a name, or use a naming convention; see
    # https://alembic.sqlalchemy.org/en/latest/naming.html
    op.drop_constraint('uq_outreach_provider_message_id', 'outreach', type_='unique')
    op.drop_index(op.f('ix_outreach_contact_id'), table_name='outreach')
    op.alter_column('outreach', 'status',
               existing_type=sa.String(length=30),
               type_=sa.VARCHAR(length=20),
               existing_nullable=False)
    op.drop_column('outreach', 'review_reason')
    op.drop_column('outreach', 'rejected_reason')
    op.drop_column('outreach', 'send_attempted_at')
    op.drop_column('outreach', 'idempotency_key')
    op.drop_column('outreach', 'postage_estimate_gbp')
    op.drop_column('outreach', 'pdf')
    op.drop_column('outreach', 'delivery_detail')
    op.drop_column('outreach', 'delivery_status')
    op.drop_column('outreach', 'provider_draft_id')
    op.drop_column('outreach', 'provider_thread_id')
    op.drop_column('outreach', 'provider_message_id')
    op.drop_column('outreach', 'provider')
    op.drop_column('outreach', 'approved_hash')
    op.drop_column('outreach', 'recipient_hash')
    op.drop_column('outreach', 'content_hash')
    op.drop_column('outreach', 'word_count')
    op.drop_column('outreach', 'generator_meta')
    op.drop_column('outreach', 'generator')
    op.drop_column('outreach', 'persona')
    op.drop_column('outreach', 'parent_id')
    op.drop_column('outreach', 'kind')
    op.drop_column('outreach', 'contact_id')
    op.drop_column('leads', 'discovered_run_id')
    op.drop_column('leads', 'last_contact_at')
    op.drop_column('leads', 'stage_changed_at')
    op.drop_column('leads', 'closed_reason')
    op.drop_column('leads', 'decline_reason')
    op.drop_column('leads', 'next_action')
    op.drop_column('leads', 'contact_method')
    op.drop_column('leads', 'benefits')
    op.drop_column('leads', 'acq_fatal')
    op.drop_column('leads', 'acq_confidence')
    op.drop_column('leads', 'acq_score_total')
    op.drop_column('leads', 'acq_score')
    op.drop_column('leads', 'analysis')
    op.drop_column('leads', 'ownership_status')
    op.drop_column('leads', 'opportunity_type')
    op.drop_index(op.f('ix_inbound_messages_lead_id'), table_name='inbound_messages')
    op.drop_table('inbound_messages')
    op.drop_index(op.f('ix_contacts_lead_id'), table_name='contacts')
    op.drop_table('contacts')
    op.drop_index(op.f('ix_title_documents_lead_id'), table_name='title_documents')
    op.drop_table('title_documents')
    op.drop_table('oauth_states')
    op.drop_table('mail_accounts')
