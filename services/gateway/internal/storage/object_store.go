// Package storage wraps MinIO/S3 for VERT's one binary-data use case:
// camera frames. Nothing else in the system should ever grow a second
// reason to reach for this package -- readings stay in the time-series
// store, everything else in Postgres.
package storage

import (
	"context"
	"io"
	"time"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"

	"github.com/vert-project/gateway/internal/config"
)

// bucketRegion is fixed and explicit rather than left for minio-go to
// discover. Without a configured region, PresignedGetObject looks it up
// with a live GetBucketLocation call against whatever client is signing
// -- which is exactly the publicClient below, whose whole reason to
// exist is that its endpoint is NOT reachable from inside this
// container. That lookup would simply fail. Pinning the region here
// means presigning is a pure local computation: no network round trip,
// and no dependency on the public endpoint being reachable from here at
// all -- only a browser, later, ever needs to reach it.
const bucketRegion = "us-east-1"

// PublicEndpointFromRequest is the VERT_S3_PUBLIC_ENDPOINT value that
// signs each photo link for whatever address the browser itself used
// (localhost, the LAN IP, a phone) -- see PresignedGetURL. It relies on
// the frontend's nginx proxying /<bucket>/ through to MinIO with the
// browser's Host header intact (frontend/nginx.conf), so no IP address
// is ever baked into configuration.
const PublicEndpointFromRequest = "request"

type ObjectStore struct {
	client       *minio.Client // internal docker-compose hostname; used for uploads
	publicClient *minio.Client // host-reachable hostname; used only to sign URLs a browser will load (nil in "request" mode)
	bucket       string
	creds        *credentials.Credentials
	useSSL       bool
}

func New(ctx context.Context, cfg config.Config) (*ObjectStore, error) {
	client, err := minio.New(cfg.S3Endpoint, &minio.Options{
		Creds:  credentials.NewStaticV4(cfg.S3AccessKey, cfg.S3SecretKey, ""),
		Secure: cfg.S3UseSSL,
		Region: bucketRegion,
	})
	if err != nil {
		return nil, err
	}

	// A presigned URL bakes its signing host into the URL itself. If we
	// always signed with the internal client, every link handed to a
	// browser would point at "minio:9000" -- a name that only resolves
	// inside the docker-compose network -- and would fail to load for
	// literally everyone. This second client exists for signing only;
	// it never uploads or checks buckets itself, and (thanks to
	// bucketRegion above) never needs to actually reach its own endpoint
	// from here to do that signing.
	creds := credentials.NewStaticV4(cfg.S3AccessKey, cfg.S3SecretKey, "")
	var publicClient *minio.Client
	if cfg.S3PublicEndpoint != PublicEndpointFromRequest {
		publicClient, err = minio.New(cfg.S3PublicEndpoint, &minio.Options{
			Creds:  creds,
			Secure: cfg.S3UseSSL,
			Region: bucketRegion,
		})
		if err != nil {
			return nil, err
		}
	}

	exists, err := client.BucketExists(ctx, cfg.S3Bucket)
	if err != nil {
		return nil, err
	}
	if !exists {
		// A fresh greenhouse install should just work the first time
		// someone plugs in a camera -- nobody should have to know MinIO
		// exists in order to see their first plant photo.
		if err := client.MakeBucket(ctx, cfg.S3Bucket, minio.MakeBucketOptions{Region: bucketRegion}); err != nil {
			return nil, err
		}
	}

	return &ObjectStore{client: client, publicClient: publicClient, bucket: cfg.S3Bucket, creds: creds, useSSL: cfg.S3UseSSL}, nil
}

// Put uploads a frame and returns the object key it was stored under.
func (s *ObjectStore) Put(ctx context.Context, key string, body io.Reader, size int64, contentType string) error {
	_, err := s.client.PutObject(ctx, s.bucket, key, body, size, minio.PutObjectOptions{
		ContentType: contentType,
	})
	return err
}

// PresignedGetURL returns a short-lived link a browser can load directly,
// so a grower checking the dashboard can actually see the photo of their
// plant rather than a bare storage key. Signed against the *public*
// endpoint -- see the comment on publicClient above for why that
// distinction matters.
//
// browserHost is the host:port the browser reached the API on. It's only
// used in "request" mode, where the link is signed for exactly that
// address: the browser then loads the photo from the same place it loaded
// the dashboard, and nginx forwards it to MinIO.
func (s *ObjectStore) PresignedGetURL(ctx context.Context, key string, expiry time.Duration, browserHost string) (string, error) {
	signer := s.publicClient
	if signer == nil {
		// Building a client is a pure local computation (no connection is
		// made, and bucketRegion skips the region lookup), so doing it per
		// request is cheap.
		var err error
		signer, err = minio.New(browserHost, &minio.Options{Creds: s.creds, Secure: s.useSSL, Region: bucketRegion})
		if err != nil {
			return "", err
		}
	}
	u, err := signer.PresignedGetObject(ctx, s.bucket, key, expiry, nil)
	if err != nil {
		return "", err
	}
	return u.String(), nil
}
