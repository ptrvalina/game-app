function errorMiddleware(error, req, res, next) {
  const status = error.statusCode || 500;
  const message = status >= 500 ? "Internal server error" : error.message;
  if (status >= 500) {
    console.error("Unhandled error", {
      message: error.message,
      path: req.path,
      method: req.method,
    });
  }
  res.status(status).json({ error: message });
}

module.exports = { errorMiddleware };
